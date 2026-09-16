from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from app.clients.pravo_ebpi import PravoEbpiClient
from app.db.models import LegalChange, LegalChangeStatus, TrackedDocument
from app.exceptions.legal_changes import LegalChangePreviewConflictError
from app.repositories.legal_changes import LegalChangesRepository
from app.schemas.processing_preview import ProcessingPreviewRequest
from app.services.processing_preview import ProcessingPreviewService
from app.services.section_text import SectionTextService
from bs4 import BeautifulSoup
from sqlalchemy import update
from tests.integration.test_admin import seed_admin_data
from tests.unit.services.test_processing_service import _redaction


@pytest.fixture
async def preview_portal(session_factory, monkeypatch, redaction_html, redaction_content_nodes):
    await seed_admin_data(session_factory)
    async with session_factory() as session:
        change = await session.get(LegalChange, 1)
        change.send_at = datetime(2027, 3, 1, tzinfo=UTC)
        change.tracked_document_id = 2  # Неактивная тестовая запись тоже допускает пробу.
        document = await session.get(TrackedDocument, 2)
        document.ebpi_doc_hash = "test-document-hash"
        await session.commit()
    portal = AsyncMock(spec=PravoEbpiClient)
    portal.get_redactions.return_value = [_redaction()]
    portal.get_redaction_text.return_value = redaction_html
    portal.get_redaction_content.return_value = redaction_content_nodes
    monkeypatch.setattr("app.admin.preview.PravoEbpiClient", lambda *args: portal)

    async def unexpected_rag(*args, **kwargs):
        raise AssertionError("Пробный запуск не должен обращаться к RAG")

    monkeypatch.setattr("app.clients.rag.RagClient.update_section", unexpected_rag)
    return portal


async def _login(client):
    await client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})


async def test_admin_preview_requires_login_and_valid_form_token(admin_client, preview_portal):
    path = "/admin/legal-change/preview/1"
    for method in (admin_client.get, admin_client.post):
        response = await method(path)
        assert response.status_code == 302
        assert response.headers["location"].endswith("/admin/login")
    await _login(admin_client)
    assert (await admin_client.post(path, data={"as_of": "2027-03-01T00:00:00"})).status_code == 403
    response = await admin_client.get(path)
    assert response.status_code == 200
    assert 'value="2027-03-01T00:00:00"' in response.text
    assert (await admin_client.post(path, data={"csrf_token": "wrong"})).status_code == 403
    assert preview_portal.mock_calls == []


async def test_admin_preview_persists_text_without_advancing_event(
    admin_client, preview_portal, session_factory,
):
    await _login(admin_client)
    details = await admin_client.get("/admin/legal-change/details/1")
    assert "Проверить извлечение статьи" in details.text
    path = "/admin/legal-change/preview/1"
    form = BeautifulSoup((await admin_client.get(path)).text, "html.parser")
    token = form.find("input", attrs={"name": "csrf_token"})["value"]
    early = await admin_client.post(path, data={"csrf_token": token, "as_of": "2027-02-28T23:59:59"})
    assert early.status_code == 200
    assert "срок события ещё не наступил" in early.text
    assert preview_portal.mock_calls == []

    for _ in range(2):
        response = await admin_client.post(path, data={"csrf_token": token, "as_of": "2027-03-01T00:00:00"})
        assert response.status_code == 200
        assert "Статья извлечена и сохранена" in response.text
        assert "Статья 57. Содержание трудового договора" in response.text
        assert "Ожидает проверки" in response.text

    async with session_factory() as session:
        change = await session.get(LegalChange, 1)
        assert change.consolidated_text.startswith("Статья 57. Содержание трудового договора")
        assert "Статья 58." not in change.consolidated_text
        assert "495396" in change.consolidated_text_source
        assert change.status == LegalChangeStatus.DRAFT
        assert change.send_at == datetime(2027, 3, 1, tzinfo=UTC)
        assert change.retry_count == 0
        assert change.sent_at is change.rag_response is change.reviewed_at is change.last_error is None
        untouched = await session.get(LegalChange, 2)
        assert untouched.consolidated_text is None
    details = await admin_client.get("/admin/legal-change/details/1")
    assert "Статья 57. Содержание трудового договора" in details.text


async def test_preview_cannot_save_over_a_concurrent_edit(session_factory, preview_portal):
    async with session_factory() as session:
        repository = LegalChangesRepository(session)
        change = await repository.get_by_id(1)
        async with session_factory() as other:
            await other.execute(
                update(LegalChange).where(LegalChange.id == 1).values(
                    consolidated_text="Текст после ручной проверки",
                    updated_at=change.updated_at + timedelta(seconds=1),
                )
            )
            await other.commit()
        assert not await repository.save_preview_text(change, "Устаревший результат", "test-source")
    async with session_factory() as session:
        assert (await session.get(LegalChange, 1)).consolidated_text == "Текст после ручной проверки"


async def test_preview_respects_real_processing_lock(session_factory, preview_portal):
    async with session_factory() as first, session_factory() as second:
        async with LegalChangesRepository(first).processing_lock() as acquired:
            assert acquired
            service = ProcessingPreviewService(LegalChangesRepository(second), SectionTextService(preview_portal))
            with pytest.raises(LegalChangePreviewConflictError, match="занята"):
                await service.preview_change(1, ProcessingPreviewRequest(as_of="2027-03-01T00:00:00Z"))
    assert preview_portal.mock_calls == []
