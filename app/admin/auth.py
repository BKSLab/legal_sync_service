from sqladmin.authentication import AuthenticationBackend
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request

from app.core.settings import get_settings


class AdminAuth(AuthenticationBackend):
    """Аутентификация sqladmin по логину и паролю из настроек."""

    def __init__(self, secret_key: str, https_only: bool = False):
        super().__init__(secret_key=secret_key)
        self.middlewares = [Middleware(SessionMiddleware, secret_key=secret_key, https_only=https_only,
                                       session_cookie='legal_sync_admin')]

    async def login(self, request: Request) -> bool:
        """Проверяет логин и пароль администратора."""

        form = await request.form()
        settings = get_settings()
        is_valid = (
            form.get("username") == settings.admin.admin_login.get_secret_value()
            and form.get("password") == settings.admin.admin_password.get_secret_value()
        )
        if is_valid:
            request.session.update({"admin_authenticated": True})
        return is_valid

    async def logout(self, request: Request) -> bool:
        """Удаляет признак входа из сессии."""

        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        """Проверяет, авторизован ли пользователь."""

        return bool(request.session.get("admin_authenticated"))
