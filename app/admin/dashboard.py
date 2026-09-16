import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.legal_changes import LegalChange, LegalChangeStatus
from app.db.models.monitoring import MonitoringLogEntry, MonitoringRun
from app.db.models.tracked_documents import TrackedDocument

logger = logging.getLogger(__name__)


@dataclass
class RecentChange:
    id: int
    document_title: str
    section_number: str
    status: LegalChangeStatus
    created_at: datetime


@dataclass
class DashboardStats:
    """Сводка реестра и очереди для административной панели."""

    postgres_ok: bool = False
    documents_total: int = 0
    documents_active: int = 0
    changes_draft: int = 0
    changes_scheduled: int = 0
    changes_processing: int = 0
    changes_failed: int = 0
    changes_created_recent: int = 0
    changes_sent_recent: int = 0
    last_sent_at: datetime | None = None
    recent_changes: list[RecentChange] = field(default_factory=list)
    last_monitoring: MonitoringRun | None = None
    last_monitoring_stage: str | None = None
    monitoring_failed_recent: int = 0


async def get_dashboard_stats(db_session: AsyncSession) -> DashboardStats:
    """Собирает счётчики и последние события, не запуская обработку очереди."""

    stats = DashboardStats()
    recent_since = datetime.now(UTC) - timedelta(hours=24)
    try:
        stats.documents_total, stats.documents_active = (
            await db_session.execute(select(
                func.count(TrackedDocument.id),
                func.count(TrackedDocument.id).filter(TrackedDocument.is_active.is_(True)),
            ))
        ).one()
        counts = dict((
            await db_session.execute(
                select(LegalChange.status, func.count()).group_by(LegalChange.status),
            )
        ).all())
        stats.changes_draft = counts.get(LegalChangeStatus.DRAFT, 0)
        stats.changes_scheduled = counts.get(LegalChangeStatus.SCHEDULED, 0)
        stats.changes_processing = counts.get(LegalChangeStatus.PROCESSING, 0)
        stats.changes_failed = counts.get(LegalChangeStatus.FAILED, 0)
        stats.changes_created_recent, stats.changes_sent_recent, stats.last_sent_at = (
            await db_session.execute(select(
                func.count(LegalChange.id).filter(LegalChange.created_at >= recent_since),
                func.count(LegalChange.id).filter(
                    LegalChange.status == LegalChangeStatus.SENT,
                    LegalChange.sent_at >= recent_since,
                ),
                func.max(LegalChange.sent_at),
            ))
        ).one()
        rows = await db_session.execute(
            select(
                LegalChange.id,
                TrackedDocument.short_name.label("document_title"),
                LegalChange.section_number,
                LegalChange.status,
                LegalChange.created_at,
            )
            .join(TrackedDocument, TrackedDocument.id == LegalChange.tracked_document_id)
            .order_by(LegalChange.created_at.desc(), LegalChange.id.desc())
            .limit(8),
        )
        stats.recent_changes = [RecentChange(**row) for row in rows.mappings()]
        # Пропуск второго воркера не подменяет результат настоящей проверки.
        stats.last_monitoring = await db_session.scalar(select(MonitoringRun).where(
            MonitoringRun.status != "skipped",
        ).order_by(MonitoringRun.id.desc()).limit(1))
        if stats.last_monitoring is not None:
            # Инициализация конфигурации ниже может сделать commit в этой же
            # SQLAdmin-сессии с expire_on_commit=True. Сводка уже прочитана.
            db_session.expunge(stats.last_monitoring)
            stats.last_monitoring_stage = await db_session.scalar(select(MonitoringLogEntry.message).where(
                MonitoringLogEntry.run_id == stats.last_monitoring.id,
            ).order_by(MonitoringLogEntry.id.desc()).limit(1))
        stats.monitoring_failed_recent = await db_session.scalar(select(func.count()).select_from(MonitoringRun).where(
            MonitoringRun.status.in_(["failed", "interrupted"]), MonitoringRun.started_at >= recent_since,
        ))
        stats.postgres_ok = True
    except (SQLAlchemyError, OSError):
        logger.exception("Не удалось получить сводку для админки Legal Sync.")
        return DashboardStats()
    return stats
