import asyncio
import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from app.core.settings import PravoEbpiSettings
from app.exceptions.pravo_ebpi import (
    PravoEbpiDocumentNotFoundError,
    PravoEbpiRequestError,
    PravoEbpiResponseError,
)
from app.schemas.pravo_ebpi import (
    EbpiContentNode,
    EbpiDocumentCard,
    EbpiRedaction,
    EbpiSearchResult,
)

logger = logging.getLogger(__name__)


class PravoEbpiClient:
    """Клиент банка консолидированных редакций actual.pravo.gov.ru.

    API недокументировано и реконструировано по фронтенду портала; полный
    разбор контракта лежит в `PRAVO_EBPI_API.md`. Клиент отдаёт наружу
    типизированные схемы и поднимает исключение при любом расхождении
    структуры ответа — молчаливый частичный разбор здесь недопустим, потому
    что результат уходит в RAG как официальный юридический текст.
    """

    SEARCH_PATH = "/attrsearch/"
    CARD_PATH = "/card/"
    REDACTIONS_PATH = "/redactions/"
    REDACTION_TEXT_PATH = "/redtext"
    CONTENT_PATH = "/getcontent/"

    # Идентификаторы атрибутов поиска из `configadd.js` портала.
    ATTR_NUMBER = 6
    ATTR_NAME = 7
    ATTR_SORT = 999

    # Режим поиска по номеру: точное совпадение.
    NUMBER_MODE_EXACT = 0
    # Режим поиска по наименованию: набор слов.
    NAME_MODE_WORDS = 9

    # Глубина выборки редакций. `3` — полная история редакций документа,
    # только она возвращает редакции старше 01.07.2022.
    REDACTIONS_FULL_HISTORY_MODE = 3
    # Режим текста редакции для банка `ebpi`.
    REDACTION_TEXT_MODE = 0

    SEARCH_PAGE_SIZE = 20
    SEARCH_SORT_FIELD = "date"

    def __init__(self, httpx_client: httpx.AsyncClient, settings: PravoEbpiSettings):
        self.httpx_client = httpx_client
        self.settings = settings
        # Портал официальный и небыстрый: параллельные запросы к нему
        # ограничиваем, чтобы мониторинг десятка документов не выглядел
        # для него всплеском нагрузки.
        self.semaphore = asyncio.Semaphore(settings.pravo_ebpi_max_concurrent_requests)

    # Блок методов поиска и карточки документа

    async def search_documents(
        self,
        document_number: str,
        title_words: str,
        page: int = 1,
    ) -> list[EbpiSearchResult]:
        """Ищет правовой акт по номеру и словам наименования.

        Args:
            document_number: Номер акта, например `197-ФЗ`.
            title_words: Наименование акта; разбивается на слова-условия.
            page: Номер страницы выдачи, начиная с 1.

        Returns:
            Список найденных актов.

        Raises:
            PravoEbpiRequestError: Портал недоступен.
            PravoEbpiResponseError: Структура ответа не соответствует ожидаемой.
        """

        logger.info(
            "🔍 Поиск акта в банке редакций. number=%s title=%s",
            document_number,
            title_words,
        )
        query = self._build_search_query(
            document_number=document_number,
            title_words=title_words,
            page=page,
        )
        data = await self._get_json(
            path=self.SEARCH_PATH,
            params={"q": json.dumps(query, ensure_ascii=False)},
        )
        documents = data.get("docs")
        if not isinstance(documents, list):
            raise PravoEbpiResponseError("В ответе поиска отсутствует список `docs`.")
        return self._validate_items(items=documents, schema=EbpiSearchResult, context="поиска")

    async def get_document_card_by_hash(self, document_hash: str) -> EbpiDocumentCard:
        """Возвращает карточку акта по его идентификатору в банке редакций.

        Нужна, чтобы получить вид акта-поправки в разобранном виде: список
        редакций отдаёт только номер и дату, а вид акта — нет.

        Args:
            document_hash: Идентификатор акта в банке редакций.

        Returns:
            Карточка акта с реквизитами принятия.

        Raises:
            PravoEbpiDocumentNotFoundError: Акта нет в банке редакций.
            PravoEbpiRequestError: Портал недоступен.
            PravoEbpiResponseError: Структура ответа не соответствует ожидаемой.
        """

        logger.info("🔍 Запрос карточки акта по идентификатору. doc_hash=%s", document_hash)
        data = await self._get_json(
            path=self.CARD_PATH,
            params={"t": json.dumps({"hash": document_hash})},
        )
        if not data.get("dochash"):
            raise PravoEbpiDocumentNotFoundError(search_key=document_hash)
        return self._validate_item(item=data, schema=EbpiDocumentCard, context="карточки акта")

    async def get_document_card(self, publication_number: str) -> EbpiDocumentCard:
        """Возвращает карточку акта по номеру официального опубликования.

        `publication_number` — это `eoNumber` из publication.pravo.gov.ru;
        именно он связывает публикацию с записью в банке редакций.

        Args:
            publication_number: Номер электронного опубликования акта.

        Returns:
            Карточка акта с его `doc_hash`.

        Raises:
            PravoEbpiDocumentNotFoundError: Акта нет в банке редакций.
            PravoEbpiRequestError: Портал недоступен.
            PravoEbpiResponseError: Структура ответа не соответствует ожидаемой.
        """

        logger.info("🔍 Запрос карточки акта в банке редакций. eo_number=%s", publication_number)
        data = await self._get_json(
            path=self.CARD_PATH,
            params={"t": json.dumps({"pnum": publication_number})},
        )
        if not data.get("dochash"):
            raise PravoEbpiDocumentNotFoundError(search_key=publication_number)
        return self._validate_item(item=data, schema=EbpiDocumentCard, context="карточки акта")

    # Блок методов работы с редакциями

    async def get_redactions(self, document_hash: str) -> list[EbpiRedaction]:
        """Возвращает полную историю редакций акта.

        Args:
            document_hash: Идентификатор акта в банке редакций.

        Returns:
            Список редакций; у каждой известна дата вступления в силу.

        Raises:
            PravoEbpiDocumentNotFoundError: Акта нет в банке редакций.
            PravoEbpiRequestError: Портал недоступен.
            PravoEbpiResponseError: Структура ответа не соответствует ожидаемой.
        """

        logger.info("🔍 Запрос редакций акта. doc_hash=%s", document_hash)
        payload = {"hash": document_hash, "ttl": self.REDACTIONS_FULL_HISTORY_MODE}
        data = await self._get_json(
            path=self.REDACTIONS_PATH,
            params={"t": json.dumps(payload)},
        )
        if data.get("error"):
            raise PravoEbpiDocumentNotFoundError(search_key=document_hash)
        redactions = data.get("redactions")
        if not isinstance(redactions, list):
            raise PravoEbpiResponseError("В ответе редакций отсутствует список `redactions`.")
        return self._validate_items(items=redactions, schema=EbpiRedaction, context="редакций")

    async def get_redaction_text(self, redaction_id: int) -> str:
        """Возвращает HTML полного текста редакции.

        Args:
            redaction_id: Идентификатор редакции.

        Returns:
            HTML редакции целиком.

        Raises:
            PravoEbpiRequestError: Портал недоступен.
            PravoEbpiResponseError: Портал не вернул текст редакции.
        """

        logger.info("🔍 Запрос текста редакции. redaction_id=%s", redaction_id)
        data = await self._get_json(
            path=self.REDACTION_TEXT_PATH,
            params={"t": redaction_id, "ttl": self.REDACTION_TEXT_MODE},
        )
        if data.get("error"):
            raise PravoEbpiResponseError(
                f"Портал вернул ошибку для редакции {redaction_id}: {data['error']}"
            )
        redaction_text = data.get("redtext")
        if not isinstance(redaction_text, str) or not redaction_text.strip():
            raise PravoEbpiResponseError(f"Пустой текст редакции {redaction_id}.")
        logger.info(
            "✅ Текст редакции получен. redaction_id=%s символов=%s",
            redaction_id,
            len(redaction_text),
        )
        return redaction_text

    async def get_redaction_content(self, redaction_id: int) -> list[EbpiContentNode]:
        """Возвращает структурное оглавление редакции.

        Args:
            redaction_id: Идентификатор редакции.

        Returns:
            Плоский список узлов оглавления с границами абзацев.

        Raises:
            PravoEbpiRequestError: Портал недоступен.
            PravoEbpiResponseError: Портал не вернул оглавление.
        """

        logger.info("🔍 Запрос оглавления редакции. redaction_id=%s", redaction_id)
        data = await self._get_json(
            path=self.CONTENT_PATH,
            params={"rdk": redaction_id},
        )
        if data.get("error"):
            raise PravoEbpiResponseError(
                f"Портал вернул ошибку оглавления для редакции {redaction_id}: {data['error']}"
            )
        nodes = data.get("data")
        if not isinstance(nodes, list) or not nodes:
            raise PravoEbpiResponseError(f"Пустое оглавление редакции {redaction_id}.")
        return self._validate_items(items=nodes, schema=EbpiContentNode, context="оглавления")

    # Блок приватных методов

    def _build_search_query(
        self,
        document_number: str,
        title_words: str,
        page: int,
    ) -> list[dict[str, Any]]:
        """Собирает набор атрибутов поиска в формате портала.

        Атрибут сортировки обязателен: без него портал возвращает счётчик
        найденных документов, но пустой список.
        """

        words = [f"+{word.lower()}" for word in title_words.split() if word.strip()]
        return [
            {
                "AttrId": self.ATTR_NUMBER,
                "AttrMode": self.NUMBER_MODE_EXACT,
                "Words": [document_number],
            },
            {
                "AttrId": self.ATTR_NAME,
                "AttrMode": self.NAME_MODE_WORDS,
                "Words": words,
            },
            {
                "AttrId": self.ATTR_SORT,
                "AttrMode": page,
                "Words": [
                    self.SEARCH_PAGE_SIZE,
                    self.SEARCH_SORT_FIELD,
                    self.settings.pravo_ebpi_search_start_date,
                    0,
                    1,
                ],
            },
        ]

    def _validate_item(self, item: Any, schema: type, context: str) -> Any:
        """Валидирует одну запись ответа портала по схеме."""

        try:
            return schema.model_validate(item)
        except ValidationError as error:
            raise PravoEbpiResponseError(
                f"Запись {context} не соответствует схеме. Подробности: {error}"
            ) from error

    def _validate_items(self, items: list[Any], schema: type, context: str) -> list[Any]:
        """Валидирует список записей ответа портала по схеме."""

        return [self._validate_item(item=item, schema=schema, context=context) for item in items]

    async def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """Выполняет GET-запрос к банку редакций с повторными попытками.

        Повторяются только сетевые ошибки, таймауты и `5xx`: на `4xx` повтор
        не имеет смысла. Худший случай одного вызова —
        `timeout * max_retries + delay * (max_retries - 1)`.
        """

        url = f"{self.settings.pravo_ebpi_base_url}{path}"
        request_params = {"bpa": self.settings.pravo_ebpi_bank, **params}
        last_error = ""

        for attempt in range(1, self.settings.pravo_ebpi_max_retries + 1):
            try:
                async with self.semaphore:
                    response = await self.httpx_client.get(
                        url,
                        params=request_params,
                        timeout=self.settings.pravo_ebpi_timeout_seconds,
                    )
                if response.status_code >= 500:
                    last_error = f"HTTP {response.status_code}"
                else:
                    response.raise_for_status()
                    return self._parse_json_body(response=response, path=path)
            except httpx.HTTPStatusError as error:
                raise PravoEbpiRequestError(
                    f"{path} вернул HTTP {error.response.status_code}."
                ) from error
            except (httpx.TimeoutException, httpx.TransportError) as error:
                last_error = f"{type(error).__name__}: {error}"

            if attempt < self.settings.pravo_ebpi_max_retries:
                logger.warning(
                    "🔄 Повтор запроса к банку редакций. path=%s попытка=%s причина=%s",
                    path,
                    attempt,
                    last_error,
                )
                await asyncio.sleep(self.settings.pravo_ebpi_retry_delay_seconds)

        logger.error(
            "❌ Банк редакций недоступен. path=%s попыток=%s причина=%s",
            path,
            self.settings.pravo_ebpi_max_retries,
            last_error,
        )
        raise PravoEbpiRequestError(f"{path} недоступен: {last_error}")

    def _parse_json_body(self, response: httpx.Response, path: str) -> dict[str, Any]:
        """Разбирает тело ответа портала как JSON-объект."""

        try:
            data = response.json()
        except ValueError as error:
            raise PravoEbpiResponseError(f"{path} вернул не JSON.") from error
        if not isinstance(data, dict):
            raise PravoEbpiResponseError(f"{path} вернул не объект, а {type(data).__name__}.")
        return data
