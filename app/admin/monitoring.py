import json
import logging
from datetime import UTC, date, datetime, time, timedelta

from fastapi.encoders import jsonable_encoder
from markupsafe import Markup, escape
from sqladmin import BaseView, expose
from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from app.db.models.monitoring import MonitoringDocumentCheck, MonitoringLogEntry, MonitoringRun

logger = logging.getLogger(__name__)
STATUSES = {
    "running": ("blue", "Выполняется"), "succeeded": ("green", "Завершён"),
    "warnings": ("yellow", "С предупреждениями"), "failed": ("red", "Ошибка"),
    "interrupted": ("red", "Прерван"), "skipped": ("secondary", "Пропущен"),
}
SOURCES = {"scheduled": "По расписанию", "manual": "Вручную через API"}
LEVELS = {"info": "Информация", "warning": "Предупреждение", "error": "Ошибка"}


def monitoring_status(value: str) -> Markup:
    color, label = STATUSES.get(value, ("secondary", value))
    return Markup(f'<span class="badge bg-{color}-lt">{escape(label)}</span>')


def positive_integer(request: Request, name: str, default: int = 1) -> int:
    try:
        value = int(request.query_params.get(name, default))
        if not 1 <= value <= 1_000_000:
            raise ValueError
        return value
    except ValueError as error:
        raise HTTPException(400, "Некорректный номер страницы или проверки документа.") from error


class MonitoringLogView(BaseView):
    name = "Журнал мониторинга"
    icon = "fa-solid fa-list-check"

    @expose("/monitoring-log", methods=["GET"])
    async def monitoring_log(self, request: Request):
        page, size = positive_integer(request, "page"), 25
        filters = {key: request.query_params.get(key, "") for key in ("status", "source", "document", "date_from", "date_to")}
        conditions = []
        for key, choices in (("status", STATUSES), ("source", SOURCES)):
            if filters[key]:
                if filters[key] not in choices:
                    raise HTTPException(400, "Неизвестный фильтр журнала.")
                conditions.append(getattr(MonitoringRun, key) == filters[key])
        if filters["document"]:
            term = f"%{filters['document']}%"
            conditions.append(select(MonitoringDocumentCheck.id).where(
                MonitoringDocumentCheck.run_id == MonitoringRun.id,
                or_(MonitoringDocumentCheck.document_id.ilike(term), MonitoringDocumentCheck.document_title.ilike(term)),
            ).exists())
        try:
            if filters["date_from"]:
                conditions.append(MonitoringRun.started_at >= datetime.combine(date.fromisoformat(filters["date_from"]), time.min, UTC))
            if filters["date_to"]:
                conditions.append(MonitoringRun.started_at < datetime.combine(date.fromisoformat(filters["date_to"]) + timedelta(days=1), time.min, UTC))
        except (ValueError, OverflowError) as error:
            raise HTTPException(400, "Укажите корректные даты фильтра.") from error
        try:
            async with self._admin_ref.session_maker() as session:
                total = await session.scalar(select(func.count()).select_from(MonitoringRun).where(*conditions))
                runs = (await session.scalars(select(MonitoringRun).where(*conditions).order_by(MonitoringRun.id.desc()).offset((page - 1) * size).limit(size))).all()
            context = {"runs": runs, "total": total, "error": None}
        except (SQLAlchemyError, OSError):
            logger.exception("Не удалось прочитать журнал мониторинга.")
            context = {"runs": [], "total": 0, "error": "Журнал недоступен: не удалось прочитать базу данных."}
        return await self.templates.TemplateResponse(request, "monitoring_log.html", {
            **context, "title": self.name, "page": page, "page_size": size,
            "filters": filters, "statuses": STATUSES, "sources": SOURCES,
        }, status_code=503 if context["error"] else 200)


class MonitoringRunView(BaseView):
    name = "Результат мониторинга"

    def is_visible(self, request: Request) -> bool:
        return False

    @expose("/monitoring-log/{run_id:int}", methods=["GET"])
    async def monitoring_run(self, request: Request):
        page, size = positive_integer(request, "page"), 100
        run_id = request.path_params["run_id"]
        check_id = positive_integer(request, "check") if request.query_params.get("check") else None
        level = request.query_params.get("level", "")
        if level and level not in LEVELS:
            raise HTTPException(400, "Неизвестный уровень журнала.")
        conditions = [MonitoringLogEntry.run_id == run_id]
        if check_id:
            conditions.append(MonitoringLogEntry.check_id == check_id)
        if level:
            conditions.append(MonitoringLogEntry.level == level)
        try:
            async with self._admin_ref.session_maker() as session:
                run = await session.get(MonitoringRun, run_id)
                if run is None:
                    raise HTTPException(404, "Запуск мониторинга не найден.")
                checks = (await session.scalars(select(MonitoringDocumentCheck).where(MonitoringDocumentCheck.run_id == run_id).order_by(MonitoringDocumentCheck.id))).all()
                total = await session.scalar(select(func.count()).select_from(MonitoringLogEntry).where(*conditions))
                entries = (await session.scalars(select(MonitoringLogEntry).where(*conditions).order_by(MonitoringLogEntry.id).offset((page - 1) * size).limit(size))).all()
        except (SQLAlchemyError, OSError) as error:
            logger.exception("Не удалось прочитать запуск мониторинга №%s.", run_id)
            raise HTTPException(503, "Журнал недоступен: не удалось прочитать базу данных.") from error
        return await self.templates.TemplateResponse(request, "monitoring_run.html", {
            "title": f"Мониторинг №{run.id}", "run": run, "checks": checks,
            "check_titles": {item.id: item.document_title for item in checks},
            "entries": entries, "total": total, "page": page, "page_size": size,
            "selected_check": check_id, "selected_level": level, "levels": LEVELS, "sources": SOURCES,
        })

    @expose("/monitoring-log/{run_id:int}/export", methods=["GET"])
    async def monitoring_export(self, request: Request):
        run_id = request.path_params["run_id"]
        try:
            async with self._admin_ref.session_maker() as session:
                # Один согласованный снимок, даже если запуск ещё выполняется.
                await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
                run = await session.get(MonitoringRun, run_id)
                if run is None:
                    raise HTTPException(404, "Запуск мониторинга не найден.")
                checks = (await session.scalars(select(MonitoringDocumentCheck).where(MonitoringDocumentCheck.run_id == run_id).order_by(MonitoringDocumentCheck.id))).all()
                entries = (await session.scalars(select(MonitoringLogEntry).where(MonitoringLogEntry.run_id == run_id).order_by(MonitoringLogEntry.id))).all()
                def record(model):
                    return {column.name: getattr(model, column.name) for column in model.__table__.columns}
                payload = {
                    "exported_at": datetime.now(UTC), "run": record(run),
                    "documents": [record(item) for item in checks], "entries": [record(item) for item in entries],
                }
        except (SQLAlchemyError, OSError) as error:
            logger.exception("Не удалось экспортировать мониторинг №%s.", run_id)
            raise HTTPException(503, "Не удалось прочитать журнал для выгрузки.") from error
        return Response(
            json.dumps(jsonable_encoder(payload), ensure_ascii=False, indent=2), media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="monitoring-{run_id}.json"', "Cache-Control": "no-store"},
        )
