import csv
import io
from datetime import UTC, date, datetime, timedelta

import pytest
from app.admin.dashboard import get_dashboard_stats
from app.db.models import LegalChange, LegalChangeStatus, TrackedDocument
from bs4 import BeautifulSoup


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
    assert "Отправка в RAG отключена" in dashboard.text

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


async def test_search_and_reset_return_records_and_reset_pagination(admin_client, session_factory):
    await seed_admin_data(session_factory)
    await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    response = await admin_client.get("/admin/tracked-document/list?search=181-ФЗ&page=1&pageSize=25")
    assert response.status_code == 200
    html = BeautifulSoup(response.text, "html.parser")
    assert len(html.select("tbody tr")) == 1
    assert "Федеральный закон № 181-ФЗ" in html.select_one("tbody").get_text()
    reset = html.find("a", string="Сбросить")["href"]
    assert "search=" not in reset and "page=" not in reset
    assert "pageSize=25" in reset
    response = await admin_client.get(reset)
    assert response.status_code == 200
    assert len(BeautifulSoup(response.text, "html.parser").select("tbody tr")) == 2


def _form_values(html):
    """Поля, которые браузер отправляет при сохранении открытой формы."""
    form = BeautifulSoup(html, "html.parser").find("form", method=lambda value: value and value.lower() == "post")
    result = {}
    for field in form.select("input[name], textarea[name], select[name]"):
        name = field["name"]
        if field.name == "textarea":
            value = field.get_text()
            # HTML-парсер браузера убирает первый перевод строки textarea.
            result[name] = value.removeprefix("\r\n") if value.startswith("\r\n") else value.removeprefix("\n")
        elif field.name == "select":
            option = field.find("option", selected=True) or field.find("option")
            if option:
                result[name] = option["value"]
        elif field.get("type") == "checkbox":
            if field.has_attr("checked"):
                result[name] = field.get("value", "on")
        elif field.get("type") != "submit":
            result[name] = field.get("value", "")
    return result


@pytest.mark.parametrize("active", [True, False])
async def test_new_document_defaults_to_monitoring_and_respects_unchecked_box(
    admin_client, session_factory, active,
):
    await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    page = await admin_client.get("/admin/tracked-document/create")
    form = _form_values(page.text)
    assert "is_active" in form
    assert form["topics"] == "[]"
    form.update(
        document_id="new-document", short_name="Новый документ", full_title="Тестовый акт",
        source_title="Тестовый источник", publication_block="president", document_number="181-ФЗ",
        adoption_date="1995-11-24", save="Save",
    )
    if not active:
        form.pop("is_active")
    response = await admin_client.post("/admin/tracked-document/create", data=form)
    assert response.status_code == 302, response.text
    async with session_factory() as session:
        record = await session.get(TrackedDocument, 1)
        assert record.is_active is active
        assert record.topics == []
    page = await admin_client.get("/admin/tracked-document/edit/1")
    assert ("is_active" in _form_values(page.text)) is active


@pytest.mark.parametrize("save,target", [
    ("Save", "/admin/tracked-document/list"),
    ("Save and continue editing", "/admin/tracked-document/edit/1"),
    ("Save and add another", "/admin/tracked-document/create"),
])
async def test_translated_save_buttons_preserve_document_data_and_redirect(
    admin_client, session_factory, save, target,
):
    await seed_admin_data(session_factory)
    await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    page = await admin_client.get("/admin/tracked-document/edit/1")
    form = _form_values(page.text)
    assert form["topics"] == "[]"
    assert "legal_changes" not in form
    assert "created_at" not in form
    form["save"] = save
    response = await admin_client.post("/admin/tracked-document/edit/1", data=form)
    assert response.status_code == 302, response.text
    assert response.headers["location"].endswith(target)
    async with session_factory() as session:
        record = await session.get(TrackedDocument, 1)
        assert record.topics == []
        assert record.document_id == "tk-197-2001"
        assert record.is_active is True
        assert (await session.get(LegalChange, 1)).tracked_document_id == 1


@pytest.mark.parametrize("change_id,expected_status", [(2, LegalChangeStatus.SCHEDULED), (4, LegalChangeStatus.FAILED), (6, LegalChangeStatus.SENT)])
async def test_saving_event_preserves_selected_status_empty_json_and_utc_time(
    admin_client, session_factory, change_id, expected_status,
):
    await seed_admin_data(session_factory)
    expected_time = datetime(2027, 3, 1, 5, 12, 13, tzinfo=UTC)
    async with session_factory() as session:
        record = await session.get(LegalChange, change_id)
        record.send_at = expected_time
        expected_topics, expected_response = record.topics_override, record.rag_response
        await session.commit()
    await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    path = f"/admin/legal-change/edit/{change_id}"
    page = await admin_client.get(path)
    form = _form_values(page.text)
    assert form["status"] == expected_status.value
    assert form["topics_override"] == ""
    assert form["send_at"] == "2027-03-01 05:12:13"
    form["save"] = "Save and continue editing"
    response = await admin_client.post(path, data=form)
    assert response.status_code == 302, response.text
    async with session_factory() as session:
        record = await session.get(LegalChange, change_id)
        assert record.status == expected_status
        assert record.send_at == expected_time
        assert record.topics_override == expected_topics
        assert record.rag_response == expected_response


async def test_detail_and_export_preserve_all_columns_and_full_article(admin_client, session_factory):
    await seed_admin_data(session_factory)
    text = "Полный текст <script>alert('x')</script>\n" + "Длинная строка " * 1500
    async with session_factory() as session:
        record = await session.get(LegalChange, 1)
        record.consolidated_text = text
        await session.commit()
    await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    response = await admin_client.get("/admin/legal-change/details/1")
    html = BeautifulSoup(response.text, "html.parser")
    visible_fields = {node["data-field"] for node in html.select("[data-field]")}
    assert set(LegalChange.__table__.columns.keys()) <= visible_fields
    assert html.select_one('[data-field="consolidated_text"] pre').get_text() == text
    assert "<script>alert('x')</script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "Извлечённый текст статьи" in response.text
    for export_type in ("json", "csv"):
        export = await admin_client.get(f"/admin/legal-change/export/{export_type}")
        assert export.status_code == 200
        rows = export.json() if export_type == "json" else list(csv.DictReader(io.StringIO(export.text)))
        record = next(row for row in rows if row["id"] == "1")
        assert record["consolidated_text"] == text
        assert record["tracked_document_id"] == "1"
        assert record["status"] == LegalChangeStatus.DRAFT.name
        assert set(LegalChange.__table__.columns.keys()) == set(record)

    details = await admin_client.get("/admin/tracked-document/details/1")
    visible_fields = {node["data-field"] for node in BeautifulSoup(details.text, "html.parser").select("[data-field]")}
    assert set(TrackedDocument.__table__.columns.keys()) <= visible_fields
    export = await admin_client.get("/admin/tracked-document/export/json")
    record = next(row for row in export.json() if row["id"] == "1")
    assert record["document_id"] == "tk-197-2001"
    assert "legal_changes" not in record
    assert set(TrackedDocument.__table__.columns.keys()) == set(record)
