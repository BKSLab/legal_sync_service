from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.admin import create_admin
from app.admin.dashboard import get_dashboard_stats
from app.core.settings import AdminSettings
from app.db.models import LegalChange, LegalChangeStatus, TrackedDocument
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


async def seed_admin_data(session_factory):
    now = datetime.now(UTC)
    recent = now - timedelta(hours=1)
    old = now - timedelta(days=2)
    async with session_factory() as session:
        tracked = TrackedDocument(
            document_id="tk-197-2001", short_name="Трудовой кодекс", full_title="Трудовой кодекс Российской Федерации",
            category="labor_code", audience="both", topics=[], source_title="Трудовой кодекс Российской Федерации",
            publication_block="president", document_number="197-ФЗ", adoption_date=date(2001, 12, 30),
            monitor_from=date(2026, 9, 14), is_active=True,
        )
        paused = TrackedDocument(
            document_id="fz-181-1995", short_name="Федеральный закон № 181-ФЗ", full_title="О социальной защите инвалидов",
            category="federal_law", audience="both", topics=[], source_title="Федеральный закон № 181-ФЗ",
            publication_block="president", document_number="181-ФЗ", adoption_date=date(1995, 11, 24),
            monitor_from=date(2026, 9, 14), is_active=False,
        )
        session.add_all([tracked, paused])
        await session.flush()
        for index, status in enumerate([
            LegalChangeStatus.DRAFT, LegalChangeStatus.SCHEDULED, LegalChangeStatus.PROCESSING,
            LegalChangeStatus.FAILED, LegalChangeStatus.CANCELLED, LegalChangeStatus.SENT, LegalChangeStatus.SENT,
        ]):
            sent = status == LegalChangeStatus.SENT
            session.add(LegalChange(
                tracked_document_id=tracked.id,
                section_number=str(57 + index),
                section_title="Условия трудового договора",
                amending_law_ref="от 26.07.2026 № 246-ФЗ",
                ebpi_redaction_id=495396,
                redaction_date=date(2026, 9, 1), effective_date=date(2026, 9, 1),
                status=status, send_at=now + timedelta(days=1),
                created_at=old if sent else recent,
                sent_at=(recent if index == 5 else old) if sent else None,
                retry_count=3 if status == LegalChangeStatus.FAILED else 0,
                last_error="RAG Service недоступен." if status == LegalChangeStatus.FAILED else None,
                rag_response={"message": "<script>alert('x')</script>"} if sent else None,
                consolidated_text="Статья 62. Условия трудового договора\nТекст принятой редакции." if sent else None,
            ))
        await session.commit()


@pytest.fixture
async def admin_client(session_factory, monkeypatch):
    settings = SimpleNamespace(admin=AdminSettings(
        _env_file=None, secret_key="admin-test-session-secret",
        admin_login="operator", admin_password="test-admin-password",
    ))
    monkeypatch.setattr("app.admin.get_settings", lambda: settings)
    monkeypatch.setattr("app.admin.auth.get_settings", lambda: settings)
    app = FastAPI()
    create_admin(app, session_factory.kw["bind"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def test_dashboard_reports_queue_and_recent_deliveries(session_factory):
    await seed_admin_data(session_factory)
    async with session_factory() as session:
        stats = await get_dashboard_stats(session)

    assert stats.postgres_ok
    assert (stats.documents_total, stats.documents_active) == (2, 1)
    assert stats.changes_draft == stats.changes_scheduled == stats.changes_processing == stats.changes_failed == 1
    assert stats.changes_created_recent == 5
    assert stats.changes_sent_recent == 1
    assert stats.last_sent_at is not None
    assert len(stats.recent_changes) == 7
    assert [row.id for row in stats.recent_changes[:5]] == [5, 4, 3, 2, 1]


async def test_admin_requires_login_and_renders_records_without_executing_document_html(
    admin_client, session_factory,
):
    await seed_admin_data(session_factory)
    for path in ("/admin/", "/admin/dashboard", "/admin/legal-change/list"):
        response = await admin_client.get(path)
        assert response.status_code == 302
        assert response.headers["location"].endswith("/admin/login")

    response = await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    assert response.status_code == 302
    dashboard = await admin_client.get("/admin/", follow_redirects=True)
    assert dashboard.status_code == 200
    assert dashboard.url.path == "/admin/dashboard"
    assert "Трудовой кодекс" in dashboard.text
    assert "Ожидает проверки" in dashboard.text

    for path in (
        "/admin/tracked-document/list", "/admin/tracked-document/details/1", "/admin/tracked-document/edit/1",
        "/admin/legal-change/list", "/admin/legal-change/create", "/admin/legal-change/edit/1",
    ):
        assert (await admin_client.get(path)).status_code == 200

    details = await admin_client.get("/admin/legal-change/details/6")
    assert details.status_code == 200
    assert "&lt;script&gt;" in details.text
    assert "<script>alert('x')</script>" not in details.text


async def test_empty_dashboard_displays_empty_state(admin_client):
    await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    response = await admin_client.get("/admin/dashboard")
    assert response.status_code == 200
    assert "Событий пока нет." in response.text
    assert "Не удалось загрузить данные" not in response.text
