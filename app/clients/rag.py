import hashlib
import logging
from datetime import date
from typing import Any

import httpx

from app.core.settings import RagSettings
from app.exceptions.rag import RagClientError, RagRejectedError, RagStaleRevisionError
from app.repositories.delivery_journal import current_delivery

logger = logging.getLogger(__name__)


class RagClient:
    """Клиент REST API RAG Service."""

    UPDATE_SECTION_PATH = "/api/v1/document/{document_id}/sections/{section_number}"

    def __init__(self, httpx_client: httpx.AsyncClient, settings: RagSettings):
        self.httpx_client = httpx_client
        self.settings = settings

    async def get_ingestion_runs(self, document_id: str, page: int = 1) -> dict:
        if self.settings.rag_service_api_key is None:
            raise RagClientError('Ключ доступа к RAG не настроен. История RAG недоступна.')
        try:
            response = await self.httpx_client.get(
                f'{self.settings.rag_service_base_url}/api/v1/ingestion-runs',
                params={'document_id': document_id, 'page': page, 'page_size': 20},
                headers={'X-API-Key': self.settings.rag_service_api_key.get_secret_value()},
                timeout=min(self.settings.rag_service_timeout_seconds, 5),
            )
            if response.status_code != 200:
                raise RagClientError(f'История RAG недоступна: HTTP {response.status_code}.')
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get('items'), list):
                raise ValueError
            return data
        except (httpx.HTTPError, ValueError) as error:
            raise RagClientError('Не удалось прочитать историю RAG.') from error

    async def update_section(
        self,
        document_id: str,
        section_number: str,
        category: str,
        raw_text: str,
        section_title: str,
        revision_date: date,
        audience: str,
        source_title: str,
        topics: list[str],
        amending_act_type: str | None = None,
        amending_act_number: str | None = None,
        amending_act_date: date | None = None,
    ) -> dict[str, Any]:
        """Отправляет актуальный текст статьи в RAG Service.

        Все поля обязательны на стороне RAG Service, поэтому клиент не
        подставляет `None` за вызывающий код: недостающие данные должны
        обнаруживаться до отправки, а не превращаться в 422 от RAG.

        Args:
            document_id: Идентификатор документа в RAG Service.
            section_number: Номер статьи, например `59`.
            category: Категория источника: labor_code, federal_law или other_npa.
            raw_text: Готовый консолидированный текст статьи.
            section_title: Заголовок статьи.
            revision_date: Дата редакции, из которой взят текст статьи.
            audience: Целевая аудитория.
            source_title: Полное официальное наименование документа.
            topics: Темы; должны быть пустыми для labor_code и federal_law.
            amending_act_type: Вид акта, которым изменена эта статья.
            amending_act_number: Номер акта, которым изменена эта статья.
            amending_act_date: Дата акта, которым изменена эта статья.

        Returns:
            Сводка обновления от RAG Service.

        Raises:
            RagRejectedError: RAG Service отклонил данные (`4xx`).
            RagClientError: RAG Service недоступен или вернул ошибку.
        """

        if self.settings.rag_service_api_key is None:
            raise RagClientError("RAG_SERVICE_API_KEY не задан.")

        path = self.UPDATE_SECTION_PATH.format(
            document_id=document_id,
            section_number=section_number,
        )
        url = f"{self.settings.rag_service_base_url}{path}"
        payload = {
            "category": category,
            "raw_text": raw_text,
            "section_title": section_title,
            "revision_date": revision_date.isoformat(),
            "audience": audience,
            "source_title": source_title,
            "topics": topics,
            "amending_act_type": amending_act_type,
            "amending_act_number": amending_act_number,
            "amending_act_date": amending_act_date.isoformat() if amending_act_date else None,
        }
        headers = {"X-API-Key": self.settings.rag_service_api_key.get_secret_value()}
        recorder = current_delivery.get()
        if recorder:
            headers['X-Request-ID'] = recorder.values['id']
            headers['X-Legal-Sync-Change-ID'] = str(recorder.values['change_id'])
            await recorder.event('waiting_rag')

        logger.info(
            "🔄 Отправка статьи в RAG. document_id=%s section=%s редакция=%s",
            document_id,
            section_number,
            revision_date,
        )
        try:
            response = await self.httpx_client.put(
                url,
                json=payload,
                headers=headers,
                timeout=self.settings.rag_service_timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            raise RagClientError(f"RAG Service недоступен: {type(error).__name__}: {error}") from error

        if recorder:
            await recorder.event('response_received', http_status=response.status_code)

        if response.status_code == 409:
            try:
                conflict = response.json()
            except ValueError:
                conflict = None
            if isinstance(conflict, dict):
                detail = conflict.get("detail")
                if (
                    isinstance(detail, dict)
                    and detail.get("code") == "stale_revision"
                    and isinstance(detail.get("message"), str)
                ):
                    raise RagStaleRevisionError(conflict)

        if 400 <= response.status_code < 500:
            # Отклонение по содержимому повторять бессмысленно: тот же текст
            # будет отклонён снова, событию нужен оператор.
            raise RagRejectedError(
                f"RAG Service отклонил статью {document_id}/{section_number}: "
                f"HTTP {response.status_code}. {response.text[:500]}"
            )
        if response.status_code >= 500:
            raise RagClientError(
                f"RAG Service вернул HTTP {response.status_code} для "
                f"{document_id}/{section_number}."
            )

        try:
            result = response.json()
        except ValueError as error:
            raise RagClientError("RAG Service вернул не JSON.") from error
        if not isinstance(result, dict):
            raise RagClientError('RAG Service вернул некорректный результат.')
        if recorder:
            await recorder.event('response_received', response={
                key: result[key] for key in (
                    'operation_id', 'status', 'warnings', 'document_id', 'section_number',
                    'version', 'chunks_count', 'superseded_chunks',
                    'collection_name', 'input_sha256', 'integrity_verified',
                ) if key in result
            })
        if result.get('operation_id'):
            expected = {
                'document_id': document_id, 'section_number': section_number, 'version': revision_date.isoformat(),
                'input_sha256': hashlib.sha256(raw_text.encode()).hexdigest(), 'integrity_verified': True,
            }
            if any(result.get(key) != value for key, value in expected.items()):
                raise RagClientError('Подтверждение RAG не соответствует отправленной статье. Проверьте журнал попытки.')
        logger.info(
            "✅ Статья принята RAG. document_id=%s section=%s",
            document_id,
            section_number,
        )
        return result
