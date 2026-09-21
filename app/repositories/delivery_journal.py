import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from app.db.models.delivery_attempt import DeliveryAttempt
from app.exceptions.legal_changes import LegalChangeRepositoryError
from app.exceptions.rag import DeliveryPaused, RagRejectedError, RagStaleRevisionError
from app.exceptions.redaction import RedactionNotReadyError

current_delivery: ContextVar['DeliveryRecorder | None'] = ContextVar('current_delivery', default=None)


class DeliveryJournal:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def recover_interrupted(self):
        # Вызывается только под общей processing_lock: другой отправитель
        # не работает. У RAG запрос мог продолжиться после остановки Sync.
        async with self.session_factory() as session, session.begin():
            await session.execute(update(DeliveryAttempt).where(DeliveryAttempt.status == 'running').values(
                status='unknown', finished_at=datetime.now(UTC), updated_at=datetime.now(UTC),
                error='Процесс отправки остановился. Проверьте RAG по номеру попытки: запрос мог быть применён.',
            ))

    @asynccontextmanager
    async def start(self, change):
        now = datetime.now(UTC)
        recorder = DeliveryRecorder(self.session_factory, dict(
            id=str(uuid4()), document_id=change.tracked_document.document_id,
            change_id=change.id, section_number=change.section_number,
            status='running', stage='preparing', started_at=now, updated_at=now,
            finished_at=None, error=None, details={'events': []},
        ))
        await recorder.event('preparing')
        token = current_delivery.set(recorder)
        try:
            yield recorder
        except BaseException as error:
            response = recorder.values['details'].get('response', {})
            if isinstance(error, DeliveryPaused):
                status = 'paused'
            elif isinstance(error, RagRejectedError | RagStaleRevisionError):
                status = 'rejected'
            elif isinstance(error, RedactionNotReadyError):
                status = 'postponed'
            elif isinstance(error, asyncio.CancelledError) or (
                recorder.values['stage'] == 'waiting_rag' and not response
            ):
                status = 'unknown'
            else:
                status = 'failed'
            recorder.values.update(status=status, error=f'{type(error).__name__}: {error}'[:4000])
            raise
        else:
            response = recorder.values['details'].get('response', {})
            confirmed = (response.get('operation_id') and response.get('integrity_verified') is True
                         and response.get('status') in ('succeeded', 'warning'))
            recorder.values['status'] = response['status'] if confirmed else 'accepted'
        finally:
            recorder.values['finished_at'] = datetime.now(UTC)
            try:
                await recorder.save()
            finally:
                current_delivery.reset(token)


class DeliveryRecorder:
    def __init__(self, session_factory, values):
        self.session_factory = session_factory
        self.values = values

    async def event(self, stage: str, **details):
        self.values['stage'] = stage
        self.values['details'].update(details)
        self.values['details']['events'].append({'stage': stage, 'at': datetime.now(UTC).isoformat()})
        await self.save()

    async def save(self):
        self.values['updated_at'] = datetime.now(UTC)
        statement = insert(DeliveryAttempt).values(**deepcopy(self.values))
        statement = statement.on_conflict_do_update(
            index_elements=[DeliveryAttempt.id],
            set_={key: getattr(statement.excluded, key) for key in self.values if key != 'id'},
        )
        try:
            async with self.session_factory() as session, session.begin():
                await session.execute(statement)
        except (SQLAlchemyError, OSError) as error:
            raise LegalChangeRepositoryError(f'Не удалось сохранить попытку доставки: {error}') from error
