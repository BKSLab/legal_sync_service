from fastapi import FastAPI
from sqladmin import Admin
from sqlalchemy.ext.asyncio import AsyncEngine

from app.admin.auth import AdminAuth
from app.admin.views import LegalChangeAdmin, TrackedDocumentAdmin
from app.core.settings import get_settings


def create_admin(app: FastAPI, engine: AsyncEngine) -> Admin:
    """Создает и подключает sqladmin к приложению."""

    settings = get_settings()
    authentication_backend = AdminAuth(
        secret_key=settings.admin.secret_key.get_secret_value(),
    )
    admin = Admin(
        app=app,
        engine=engine,
        authentication_backend=authentication_backend,
        title="Legal Sync Admin",
    )
    admin.add_view(TrackedDocumentAdmin)
    admin.add_view(LegalChangeAdmin)
    return admin
