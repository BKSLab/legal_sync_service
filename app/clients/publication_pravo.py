import logging
from typing import Any

import httpx

from app.core.settings import PublicationPravoSettings

logger = logging.getLogger(__name__)


class PublicationPravoClient:
    """Клиент публичного API publication.pravo.gov.ru."""

    DOCUMENTS_PATH = "/api/Documents"
    FILE_PATH_TEMPLATE = "/File/GetFile/{eo_number}"

    def __init__(self, httpx_client: httpx.AsyncClient, settings: PublicationPravoSettings):
        self.httpx_client = httpx_client
        self.settings = settings

    async def get_daily_documents(self, publication_block: str, page_size: int = 200) -> list[dict[str, Any]]:
        """Получает документы дневной публикации по блоку публикации."""

        url = f"{self.settings.publication_pravo_base_url}{self.DOCUMENTS_PATH}"
        params = {
            "PeriodType": "daily",
            "Block": publication_block,
            "PageSize": page_size,
        }
        logger.info("🔍 Запрос публикаций publication.pravo.gov.ru. block=%s", publication_block)
        response = await self.httpx_client.get(
            url,
            params=params,
            timeout=self.settings.publication_pravo_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            items = data.get("items") or data.get("documents") or data.get("data") or []
            return items if isinstance(items, list) else []
        return []

    async def download_pdf_by_eo_number(self, eo_number: str) -> bytes:
        """Скачивает PDF закона-поправки по eoNumber.

        URL-паттерн требует ручного подтверждения по ТЗ.
        """

        path = self.FILE_PATH_TEMPLATE.format(eo_number=eo_number)
        url = f"{self.settings.publication_pravo_base_url}{path}"
        logger.info("🔍 Скачивание PDF закона-поправки. eo_number=%s", eo_number)
        response = await self.httpx_client.get(
            url,
            timeout=self.settings.publication_pravo_timeout_seconds,
        )
        response.raise_for_status()
        return response.content
