import logging
from datetime import date
from typing import Any

import httpx

from app.core.settings import RagSettings

logger = logging.getLogger(__name__)


class RagClient:
    """Клиент REST API RAG Service."""

    UPDATE_SECTION_PATH = "/document/{document_id}/sections/{section_number}"

    def __init__(self, httpx_client: httpx.AsyncClient, settings: RagSettings):
        self.httpx_client = httpx_client
        self.settings = settings

    async def update_section(
        self,
        document_id: str,
        section_number: str,
        category: str,
        raw_text: str,
        section_title: str | None,
        version: str,
        effective_date: date | None,
        audience: str,
        topic: str,
        source_title: str,
    ) -> dict[str, Any]:
        """Отправляет актуальный текст статьи в RAG Service."""

        if self.settings.rag_service_api_key is None:
            raise RuntimeError("RAG_SERVICE_API_KEY не задан.")

        path = self.UPDATE_SECTION_PATH.format(
            document_id=document_id,
            section_number=section_number,
        )
        url = f"{self.settings.rag_service_base_url}{path}"
        payload = {
            "category": category,
            "raw_text": raw_text,
            "section_title": section_title,
            "version": version,
            "effective_date": effective_date.isoformat() if effective_date else None,
            "audience": audience,
            "topic": topic,
            "source_title": source_title,
        }
        headers = {
            "X-API-Key": self.settings.rag_service_api_key.get_secret_value(),
        }
        logger.info("🔄 Отправка статьи в RAG. document_id=%s section=%s", document_id, section_number)
        response = await self.httpx_client.put(
            url,
            json=payload,
            headers=headers,
            timeout=self.settings.rag_service_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()
