from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

import pytest
from app.clients.pravo_ebpi import PravoEbpiClient
from app.db.models import LegalChange, LegalChangeStatus, TrackedDocument
from app.exceptions.legal_changes import LegalChangeReviewConflictError
from app.repositories.legal_changes import LegalChangesRepository
from bs4 import BeautifulSoup
from tests.integration.test_admin import seed_admin_data
from tests.unit.services.test_change_review_service import redaction


async def login(client):
    await client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})


async def form_values(client):
    page = await client.get("/admin/legal-change/details/1")
    assert page.status_code == 200
    form = BeautifulSoup(page.text, "html.parser").select_one("form[action$='/legal-change/review/1']")
    return {field["name"]: field.get("value", "") for field in form.select("input[name]")}


async def test_review_requires_login_csrf_and_a_current_card(admin_client, session_factory):
    await seed_admin_data(session_factory)
    path = "/admin/legal-change/review/1"
    assert (await admin_client.post(path, data={"decision": "approve"})).status_code == 302
    await login(admin_client)
    values = await form_values(admin_client)
    assert (await admin_client.post(path, data={**values, "csrf_token": "wrong", "decision": "approve"})).status_code == 403
    async with session_factory() as session:
        change = await session.get(LegalChange, 1)
        change.effective_date = date(2027, 9, 1)
        await session.commit()
    response = await admin_client.post(path, data={**values, "decision": "approve"})
    assert response.status_code == 409
    assert "Событие уже изменилось" in response.text
    async with session_factory() as session:
        assert (await session.get(LegalChange, 1)).status == LegalChangeStatus.DRAFT


async def test_approval_schedules_and_audits_without_sending(admin_client, session_factory, monkeypatch):
    await seed_admin_data(session_factory)
    portal = AsyncMock(side_effect=AssertionError("Approval must not fetch the portal"))
    monkeypatch.setattr(PravoEbpiClient, "get_redactions", portal)
    await login(admin_client)
    values = await form_values(admin_client)
    payload = {**values, "decision": "approve", "effective_date": "2027-03-01", "review_notes": "Проверены текст и дата"}
    response = await admin_client.post("/admin/legal-change/review/1", data=payload, follow_redirects=True)
    assert response.status_code == 200
    assert "подтверждено и поставлено" in response.text
    async with session_factory() as session:
        change = await session.get(LegalChange, 1)
        assert change.status == LegalChangeStatus.SCHEDULED
        assert change.send_at == datetime(2027, 3, 1, tzinfo=UTC)
        assert change.reviewed_by == "operator" and change.reviewed_at
        assert change.review_notes == payload["review_notes"]
        assert change.sent_at is None and change.retry_count == 0
    duplicate = await admin_client.post("/admin/legal-change/review/1", data=payload)
    assert duplicate.status_code == 409


async def test_rejection_records_operator_and_does_not_require_effective_date(admin_client, session_factory):
    await seed_admin_data(session_factory)
    await login(admin_client)
    values = await form_values(admin_client)
    response = await admin_client.post("/admin/legal-change/review/1", data={
        **values, "decision": "reject", "effective_date": "", "review_notes": "Изменение не подходит",
    }, follow_redirects=True)
    assert response.status_code == 200
    async with session_factory() as session:
        change = await session.get(LegalChange, 1)
        assert change.status == LegalChangeStatus.CANCELLED
        assert change.reviewed_by == "operator" and change.reviewed_at
        assert change.sent_at is None


async def test_approval_requires_a_valid_date(admin_client, session_factory):
    await seed_admin_data(session_factory)
    await login(admin_client)
    values = await form_values(admin_client)
    for date_value in ("", "not-a-date"):
        response = await admin_client.post("/admin/legal-change/review/1", data={
            **values, "decision": "approve", "effective_date": date_value,
        })
        assert response.status_code == 422
    async with session_factory() as session:
        assert (await session.get(LegalChange, 1)).status == LegalChangeStatus.DRAFT


async def test_reading_comparison_is_escaped_and_never_changes_event(admin_client, session_factory, monkeypatch):
    await seed_admin_data(session_factory)
    async with session_factory() as session:
        document = await session.get(TrackedDocument, 1)
        document.ebpi_doc_hash = "b" * 64
        change = await session.get(LegalChange, 1)
        change.amending_doc_hash = "a" * 64
        await session.commit()
        await session.refresh(change)
        original_updated_at = change.updated_at
    source = [redaction(900000, "20270201", "1. Предыдущая"), redaction(495396, "20270301", "2. Событие")]
    monkeypatch.setattr(PravoEbpiClient, "get_redactions", AsyncMock(return_value=source))
    monkeypatch.setattr(PravoEbpiClient, "get_redaction_text", AsyncMock(side_effect=[
        '<p id="p1">Статья 57.</p><p id="p2">Новая &lt;script&gt;опасная&lt;/script&gt; формулировка</p>',
        '<p id="p1">Статья 57.</p><p id="p2">Старая формулировка</p>',
    ]))
    from app.schemas.pravo_ebpi import EbpiContentNode
    monkeypatch.setattr(PravoEbpiClient, "get_redaction_content", AsyncMock(return_value=[
        EbpiContentNode(id="article57", caption="Статья 57.", unit="статья", lvl=1, np="p1", npe="p2"),
    ]))
    await login(admin_client)
    response = await admin_client.get("/admin/legal-change/details/1?compare=1")
    assert response.status_code == 200
    assert "Старая формулировка" in response.text and "Новая &lt;script&gt;" in response.text
    assert "<script>опасная</script>" not in response.text
    page = BeautifulSoup(response.text, "html.parser")
    assert page.find("a", string="Открыть закон-поправку на pravo.gov.ru")["href"].endswith("#hash=" + "a" * 64 + "&ttl=3")
    async with session_factory() as session:
        change = await session.get(LegalChange, 1)
        assert change.status == LegalChangeStatus.DRAFT and change.updated_at == original_updated_at
        assert change.consolidated_text is change.reviewed_at is change.sent_at is None


async def test_second_review_cannot_overwrite_the_first(session_factory):
    await seed_admin_data(session_factory)
    async with session_factory() as first, session_factory() as second:
        repo1, repo2 = LegalChangesRepository(first), LegalChangesRepository(second)
        change1, change2 = await repo1.get_by_id(1), await repo2.get_by_id(1)
        await repo1.save_review(change1, {"status": LegalChangeStatus.SCHEDULED, "reviewed_by": "first"})
        with pytest.raises(LegalChangeReviewConflictError):
            await repo2.save_review(change2, {"status": LegalChangeStatus.CANCELLED, "reviewed_by": "second"})
    async with session_factory() as session:
        change = await session.get(LegalChange, 1)
        assert change.status == LegalChangeStatus.SCHEDULED and change.reviewed_by == "first"
