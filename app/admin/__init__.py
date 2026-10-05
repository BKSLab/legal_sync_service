from hashlib import sha256
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqladmin import Admin
from sqladmin.authentication import login_required
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.admin.auth import AdminAuth
from app.admin.automation import AutomationView
from app.admin.change_review import ChangeReviewView, render_change_details
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

    @login_required
    async def details(self, request: Request):
        if request.path_params["identity"] != "legal-change":
            return await super().details(request)
        await self._details(request)
        view = self._find_model_view("legal-change")
        model = await view.get_object_for_details(request.path_params["pk"])
        if model is None:
            raise HTTPException(404, "Событие не найдено.")
        return await render_change_details(self, request, model)


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
    # New asset URLs prevent cached styles/scripts surviving an application update.
    admin.templates.env.globals["asset_versions"] = {
        path.name: sha256(path.read_bytes()).hexdigest()[:16]
        for path in (_APP_DIR / "static").iterdir()
        if path.suffix in {".css", ".js"}
    }
    admin.templates.env.filters["change_status"] = format_change_status
    admin.templates.env.filters["monitoring_status"] = monitoring_status
    admin.templates.env.policies["json.dumps_kwargs"] = {"sort_keys": True, "ensure_ascii": False}
    admin.add_view(DashboardView)
    admin.add_view(AutomationView)
    admin.add_view(DocumentTraceView)
    admin.add_view(TrackedDocumentAdmin)
    admin.add_view(LegalChangeAdmin)
    admin.add_view(MonitoringLogView)
    admin.add_view(MonitoringRunView)
    admin.add_view(ConfigurationView)
    admin.add_view(DocumentationView)
    admin.add_view(ChangePreviewView)
    admin.add_view(ChangeReviewView)
    return admin
