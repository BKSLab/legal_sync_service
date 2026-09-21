from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqladmin import Admin
from sqladmin.authentication import login_required
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.admin.auth import AdminAuth
from app.admin.configuration import ConfigurationView
from app.admin.document_trace import DocumentTraceView
from app.admin.documentation import DocumentationView
from app.admin.monitoring import MonitoringLogView, MonitoringRunView, monitoring_status
from app.admin.preview import ChangePreviewView
from app.admin.views import (
    DashboardView,
    LegalChangeAdmin,
    TrackedDocumentAdmin,
    format_change_status,
)
from app.core.settings import get_settings

_APP_DIR = Path(__file__).resolve().parent.parent


class LegalSyncAdmin(Admin):
    """Главная страница открывает сводку мониторинга."""

    @login_required
    async def index(self, request: Request) -> RedirectResponse:
        return RedirectResponse(request.url_for("admin:dashboard"), status_code=303)


def create_admin(app: FastAPI, engine: AsyncEngine) -> Admin:
    """Создает и подключает sqladmin к приложению."""

    settings = get_settings()
    authentication_backend = AdminAuth(
        secret_key=settings.admin.secret_key.get_secret_value(),
        https_only=settings.admin.admin_session_https_only,
    )
    app.mount("/static", StaticFiles(directory=_APP_DIR / "static"), name="static")
    admin = LegalSyncAdmin(
        app=app,
        engine=engine,
        authentication_backend=authentication_backend,
        title="Legal Sync — Администрирование",
        base_url="/admin",
        templates_dir=str(_APP_DIR / "templates"),
    )
    admin.templates.env.filters["change_status"] = format_change_status
    admin.templates.env.filters["monitoring_status"] = monitoring_status
    admin.templates.env.policies["json.dumps_kwargs"] = {"sort_keys": True, "ensure_ascii": False}
    admin.add_view(DashboardView)
    admin.add_view(DocumentTraceView)
    admin.add_view(TrackedDocumentAdmin)
    admin.add_view(LegalChangeAdmin)
    admin.add_view(MonitoringLogView)
    admin.add_view(MonitoringRunView)
    admin.add_view(ConfigurationView)
    admin.add_view(DocumentationView)
    admin.add_view(ChangePreviewView)
    return admin
