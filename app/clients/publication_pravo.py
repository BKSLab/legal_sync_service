import logging
from typing import Any

import httpx

from app.core.settings import PublicationPravoSettings

logger = logging.getLogger(__name__)


class PublicationPravoClient:
    """Клиент публичного API publication.pravo.gov.ru."""

    PUBLIC_BLOCKS_PATH = "/api/PublicBlocks"
    CATEGORIES_PATH = "/api/Categories"
    SIGNATORY_AUTHORITIES_PATH = "/api/SignatoryAuthorities"
    DOCUMENT_TYPES_PATH = "/api/DocumentTypes"
    DOCUMENTS_PATH = "/api/Documents"
    DOCUMENT_PATH = "/api/Document"
    DOCUMENT_TEXT_PATH = "/api/DocumentText"
    BLOCK_STATISTICS_PATH_TEMPLATE = "/api/BlockStatistics/{period}"
    PDF_PATH = "/file/pdf"

    def __init__(self, httpx_client: httpx.AsyncClient, settings: PublicationPravoSettings):
        self.httpx_client = httpx_client
        self.settings = settings

    async def get_public_blocks(self, parent: str | None = None) -> list[dict[str, Any]]:
        """Получает список блоков публикации и подблоков."""

        params = {"parent": parent} if parent else None
        data = await self._get_json(path=self.PUBLIC_BLOCKS_PATH, params=params)
        return data if isinstance(data, list) else []

    async def get_categories(self, block: str) -> list[dict[str, Any]]:
        """Получает категории принявших органов для блока публикации."""

        data = await self._get_json(path=self.CATEGORIES_PATH, params={"block": block})
        return data if isinstance(data, list) else []

    async def get_signatory_authorities(
        self,
        block: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Получает список принявших органов."""

        params = {}
        if block:
            params["block"] = block
        if category:
            params["category"] = category
        data = await self._get_json(path=self.SIGNATORY_AUTHORITIES_PATH, params=params or None)
        return data if isinstance(data, list) else []

    async def get_document_types(
        self,
        block: str | None = None,
        category: str | None = None,
        signatory_authority_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Получает список видов документов."""

        params = {}
        if block:
            params["block"] = block
        if category:
            params["category"] = category
        if signatory_authority_id:
            params["SignatoryAuthorityId"] = signatory_authority_id
        data = await self._get_json(path=self.DOCUMENT_TYPES_PATH, params=params or None)
        return data if isinstance(data, list) else []

    async def get_daily_documents(
        self,
        publication_block: str,
        page_size: int = 200,
        document_type_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Получает документы дневной публикации по блоку публикации."""

        params = {
            "PeriodType": "daily",
            "Block": publication_block,
            "PageSize": page_size,
        }
        if document_type_id:
            params["DocumentTypes"] = document_type_id
        logger.info("🔍 Запрос публикаций publication.pravo.gov.ru. block=%s", publication_block)
        data = await self._get_json(path=self.DOCUMENTS_PATH, params=params)
        if isinstance(data, dict):
            items = data.get("items") or []
            return items if isinstance(items, list) else []
        return []

    async def get_document(self, eo_number: str) -> dict[str, Any]:
        """Получает расширенные параметры документа по номеру опубликования."""

        data = await self._get_json(path=self.DOCUMENT_PATH, params={"eoNumber": eo_number})
        return data if isinstance(data, dict) else {}

    async def get_block_statistics(self, period: str = "daily") -> list[dict[str, Any]]:
        """Получает статистику опубликования документов по блокам."""

        path = self.BLOCK_STATISTICS_PATH_TEMPLATE.format(period=period)
        data = await self._get_json(path=path)
        return data if isinstance(data, list) else []

    async def download_pdf_by_eo_number(self, eo_number: str) -> bytes:
        """Скачивает PDF документа по номеру электронного опубликования."""

        url = f"{self.settings.publication_pravo_base_url}{self.PDF_PATH}"
        logger.info("🔍 Скачивание PDF закона-поправки. eo_number=%s", eo_number)
        response = await self.httpx_client.get(
            url,
            params={"eoNumber": eo_number},
            timeout=self.settings.publication_pravo_timeout_seconds,
        )
        response.raise_for_status()
        return response.content

    async def get_document_text(self, eo_number: str) -> Any:
        """Получает текст документа из endpoint страницы документа."""

        return await self._get_json(
            path=self.DOCUMENT_TEXT_PATH,
            params={"eonumber": eo_number},
        )

    async def _get_json(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Выполняет GET-запрос и возвращает JSON-ответ."""

        url = f"{self.settings.publication_pravo_base_url}{path}"
        response = await self.httpx_client.get(
            url,
            params=params,
            timeout=self.settings.publication_pravo_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()
