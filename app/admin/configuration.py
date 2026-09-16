import logging
import secrets
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError
from sqladmin import BaseView, expose
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.core.settings import get_settings
from app.exceptions.configuration import ConfigurationConflictError
from app.repositories.configuration import ConfigurationRepository
from app.schemas.configuration import ConfigurationValues
from app.services.configuration import initial_configuration

logger = logging.getLogger(__name__)

CONFIGURATION_LABELS = {
    "rag_delivery_enabled": "Отправка в RAG",
    "monitoring_enabled": "Мониторинг по расписанию",
    "monitoring_cron_hour": "Расписание мониторинга",
    "processing_cron_hour": "Расписание отправки",
    "timezone": "Часовой пояс расписаний",
    "processing_max_retries": "Лимит попыток отправки",
}
SCHEDULE_CHOICES = {
    "*": "Каждый час",
    "*/2": "Каждые 2 часа",
    "*/3": "Каждые 3 часа",
    "*/6": "Каждые 6 часов",
    "*/12": "Каждые 12 часов",
    **{str(hour): f"Ежедневно в {hour:02d}:00" for hour in range(24)},
}


def display_configuration_value(name: str, value) -> str:
    if isinstance(value, bool):
        return "Включено" if value else "Выключено"
    if name.endswith("cron_hour"):
        return SCHEDULE_CHOICES.get(str(value), f"Часы запуска: {value}")
    return str(value)


def next_runs(configuration, scheduler_enabled: bool) -> dict[str, str]:
    result = {}
    for name, enabled, hour in (
        ("monitoring", configuration.monitoring_enabled, configuration.monitoring_cron_hour),
        ("processing", configuration.rag_delivery_enabled, configuration.processing_cron_hour),
    ):
        if not scheduler_enabled or not enabled:
            result[name] = "Не запланирован"
        else:
            trigger = CronTrigger(hour=hour, minute=0, timezone=configuration.timezone)
            next_time = trigger.get_next_fire_time(None, datetime.now(UTC))
            result[name] = next_time.strftime("%d.%m.%Y %H:%M") if next_time else "Не запланирован"
    return result


def safe_service_address(address: str) -> str:
    """На страницу не попадают возможные credentials или query-параметры URL."""
    try:
        parts = urlsplit(address)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = f":{parts.port}" if parts.port else ""
        return urlunsplit((parts.scheme, host + port, parts.path, "", ""))
    except ValueError:
        return "Адрес задан некорректно"


class ConfigurationView(BaseView):
    name = "Конфигурация"
    icon = "fa-solid fa-sliders"

    @expose("/configuration", methods=["GET", "POST"])
    async def configuration(self, request: Request):
        settings = get_settings()
        form = await request.form() if request.method == "POST" else None
        token = request.session.get("configuration_csrf_token", "")
        if form is not None:
            submitted = form.get("csrf_token", "")
            if not token or not isinstance(submitted, str) or not secrets.compare_digest(token.encode(), submitted.encode()):
                raise HTTPException(403, "Обновите страницу конфигурации и повторите запрос.")
        if not token:
            token = secrets.token_urlsafe(32)
            request.session["configuration_csrf_token"] = token

        key = settings.rag.rag_service_api_key
        key_configured = bool(key and key.get_secret_value().strip())
        context = {
            "title": "Конфигурация", "configuration": None, "errors": {}, "error": None,
            "csrf_token": token, "history": [], "conflict": False,
            "labels": CONFIGURATION_LABELS, "display_value": display_configuration_value,
            "scheduler_enabled": settings.scheduler.scheduler_enabled,
            "rag_address": safe_service_address(settings.rag.rag_service_base_url),
            "rag_key_configured": key_configured,
        }
        status_code = 200
        try:
            async with self._admin_ref.session_maker() as session:
                repository = ConfigurationRepository(session)
                current = await repository.get_or_create(initial_configuration(settings))
                values = current.model_dump()
                if form is not None:
                    values = {
                        "rag_delivery_enabled": form.get("rag_delivery_enabled") == "on",
                        "monitoring_enabled": form.get("monitoring_enabled") == "on",
                        **{name: str(form.get(name, "")) for name in (
                            "monitoring_cron_hour", "processing_cron_hour", "timezone", "processing_max_retries",
                        )},
                    }
                    try:
                        submitted_values = ConfigurationValues.model_validate(values)
                    except ValidationError as error:
                        status_code = 422
                        for item in error.errors():
                            name = item["loc"][0]
                            context["errors"][name] = {
                                "timezone": "Укажите существующий часовой пояс, например Europe/Moscow.",
                                "processing_max_retries": "Укажите целое число от 1 до 20.",
                            }.get(name, "Выберите корректное расписание.")
                    if values["rag_delivery_enabled"] and not key_configured:
                        context["errors"]["rag_delivery_enabled"] = "Для включения отправки задайте RAG_SERVICE_API_KEY на сервере."
                        status_code = 422
                    try:
                        version = int(str(form.get("version", "")))
                    except ValueError:
                        version = 0
                    values["version"] = version
                    if not context["errors"]:
                        try:
                            saved = await repository.save(
                                submitted_values, version, settings.admin.admin_login.get_secret_value(),
                            )
                        except ConfigurationConflictError:
                            status_code = 409
                            context["conflict"] = True
                            context["error"] = "Конфигурация уже изменена в другой вкладке. Загрузите актуальные настройки перед сохранением."
                        else:
                            logger.info("Конфигурация сохранена: версия=%s, отправка в RAG=%s.", saved.version, saved.rag_delivery_enabled)
                            return RedirectResponse(str(request.url_for("admin:configuration")) + "?saved=1", status_code=303)
                choices = dict(SCHEDULE_CHOICES)
                for name in ("monitoring_cron_hour", "processing_cron_hour"):
                    hour = str(values[name])
                    # Сохраняем совместимость с существующим расписанием из окружения.
                    choices.setdefault(hour, display_configuration_value(name, hour))
                context.update(
                    configuration=current, values=values, schedule_choices=choices,
                    history=await repository.history(), next_runs=next_runs(current, settings.scheduler.scheduler_enabled),
                )
        except (SQLAlchemyError, OSError):
            logger.exception("Ошибка чтения или сохранения конфигурации в админке.")
            context["error"] = "Конфигурация недоступна. Изменения не сохранены; обновите страницу и повторите попытку."
            context["configuration"] = None
            status_code = 503
        return await self.templates.TemplateResponse(request, "configuration.html", context, status_code=status_code)
