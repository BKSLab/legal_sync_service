from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, time

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.db.models.legal_changes import LegalChange, LegalChangeStatus
from app.exceptions.legal_changes import LegalChangeRepositoryError
from app.schemas.legal_changes import LegalChangeCreateRequest

PROCESSING_LOCK_KEY = 8_401_002


class LegalChangesRepository:
    """Репозиторий событий изменений."""

    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    def _build_values(self, data: LegalChangeCreateRequest) -> dict:
        """Готовит поля события, рассчитывая срок отправки по дате вступления в силу."""

        values = data.model_dump()
        if values.get("effective_date"):
            values["send_at"] = datetime.combine(values["effective_date"], time.min, tzinfo=UTC)
        return values

    async def save(self, data: LegalChangeCreateRequest) -> LegalChange:
        """Сохраняет новое событие изменения."""

        change = LegalChange(**self._build_values(data=data))
        try:
            self.db_session.add(change)
            await self.db_session.commit()
            await self.db_session.refresh(change)
            return change
        except IntegrityError as error:
            await self.db_session.rollback()
            raise LegalChangeRepositoryError("Событие уже существует.") from error
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise LegalChangeRepositoryError(str(error)) from error

    async def save_redaction_changes(self, data: list[LegalChangeCreateRequest]) -> int:
        """Сохраняет все статьи редакции одной транзакцией без дубликатов.

        Редакция считается известной по наличию её событий, поэтому коммит
        отдельной статьи недопустим: сбой потерял бы оставшиеся статьи.
        Конфликт именно ключа редакции пропускается; прочие ошибки БД
        откатывают весь набор и позволяют повторить его следующим запуском.
        """
        if not data:
            return 0
        try:
            created = 0
            # Откат записи не должен сбрасывать уже загруженные карточки
            # документов в сессии мониторинга: он продолжит обход остальных.
            async with AsyncSession(bind=self.db_session.bind) as session:
                async with session.begin():
                    for change in data:
                        result = await session.execute(
                            insert(LegalChange)
                            .values(**self._build_values(data=change))
                            .on_conflict_do_nothing(
                                constraint="unique_legal_changes_document_redaction_section",
                            )
                            .returning(LegalChange.id)
                        )
                        created += result.scalar_one_or_none() is not None
            return created
        except SQLAlchemyError as error:
            raise LegalChangeRepositoryError(str(error)) from error

    @asynccontextmanager
    async def processing_lock(self) -> AsyncIterator[bool]:
        """Общая блокировка очереди для планировщика и ручного запуска.

        Отдельная транзакция не завершается при сохранении статусов событий.
        PostgreSQL освобождает её блокировку и при остановке процесса.
        """
        async with AsyncSession(bind=self.db_session.bind) as lock_session:
            async with lock_session.begin():
                result = await lock_session.execute(
                    text("SELECT pg_try_advisory_xact_lock(:key)"),
                    {"key": PROCESSING_LOCK_KEY},
                )
                yield bool(result.scalar_one())

    async def recover_interrupted_processing(self) -> int:
        """Возвращает прерванные попытки в очередь под processing_lock.

        Владелец общей блокировки единственный обрабатывает очередь, поэтому
        оставшиеся `processing` принадлежат уже завершившемуся запуску.
        Доставка могла состояться: RAG допускает повтор той же редакции.
        """
        try:
            result = await self.db_session.execute(
                update(LegalChange)
                .where(LegalChange.status == LegalChangeStatus.PROCESSING)
                .values(
                    status=LegalChangeStatus.SCHEDULED,
                    last_error="Предыдущая обработка прервана; отправка будет повторена.",
                )
                .returning(LegalChange.id)
            )
            recovered = len(result.scalars().all())
            await self.db_session.commit()
            return recovered
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise LegalChangeRepositoryError(str(error)) from error

    async def get_known_redaction_ids(self, tracked_document_id: int) -> set[int]:
        """Возвращает идентификаторы редакций, по которым события уже создавались.

        Args:
            tracked_document_id: Внутренний ID отслеживаемого документа.

        Returns:
            Множество идентификаторов редакций.

        Raises:
            LegalChangeRepositoryError: Ошибка базы данных.
        """

        stmt = (
            select(LegalChange.ebpi_redaction_id)
            .where(
                LegalChange.tracked_document_id == tracked_document_id,
                LegalChange.ebpi_redaction_id.is_not(None),
            )
            .distinct()
        )
        try:
            result = await self.db_session.execute(stmt)
            return {row for row in result.scalars().all() if row is not None}
        except SQLAlchemyError as error:
            raise LegalChangeRepositoryError(str(error)) from error

    async def get_due_for_sending(self, now: datetime, max_retries: int) -> list[LegalChange]:
        """Возвращает события, готовые к отправке в RAG Service.

        Отслеживаемый документ загружается сразу: обработчику нужны его
        реквизиты для формирования запроса, а догружать связь за пределами
        репозитория запрещено.

        Args:
            now: Момент, на который отбираются события.
            max_retries: Предел попыток, после которого событие не повторяется.

        Returns:
            События в статусах `scheduled` и `failed`, у которых наступил срок.

        Raises:
            LegalChangeRepositoryError: Ошибка базы данных.
        """

        stmt = (
            select(LegalChange)
            .options(joinedload(LegalChange.tracked_document))
            .where(
                LegalChange.status.in_(
                    [LegalChangeStatus.SCHEDULED, LegalChangeStatus.FAILED],
                ),
                LegalChange.send_at.is_not(None),
                LegalChange.send_at <= now,
                LegalChange.retry_count < max_retries,
            )
            .order_by(LegalChange.send_at, LegalChange.id)
        )
        try:
            result = await self.db_session.execute(stmt)
            return list(result.scalars().all())
        except SQLAlchemyError as error:
            raise LegalChangeRepositoryError(str(error)) from error

    async def get_by_id(self, change_id: int) -> LegalChange | None:
        """Возвращает событие по ID."""

        try:
            result = await self.db_session.execute(
                select(LegalChange)
                .options(joinedload(LegalChange.tracked_document))
                .where(LegalChange.id == change_id)
            )
            return result.scalar_one_or_none()
        except SQLAlchemyError as error:
            raise LegalChangeRepositoryError(str(error)) from error

    async def get_list(
        self,
        page: int,
        page_size: int,
        status: LegalChangeStatus | None = None,
    ) -> list[LegalChange]:
        """Возвращает список событий с пагинацией."""

        stmt = select(LegalChange).order_by(LegalChange.id.desc())
        if status is not None:
            stmt = stmt.where(LegalChange.status == status)
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)

        try:
            result = await self.db_session.execute(stmt)
            return list(result.scalars().all())
        except SQLAlchemyError as error:
            raise LegalChangeRepositoryError(str(error)) from error

    async def get_count(self, status: LegalChangeStatus | None = None) -> int:
        """Возвращает количество событий."""

        stmt = select(func.count()).select_from(LegalChange)
        if status is not None:
            stmt = stmt.where(LegalChange.status == status)

        try:
            result = await self.db_session.execute(stmt)
            return int(result.scalar_one())
        except SQLAlchemyError as error:
            raise LegalChangeRepositoryError(str(error)) from error

    async def save_preview_text(
        self, change: LegalChange, extracted_text: str, source: str,
    ) -> bool:
        """Сохраняет текст, только если событие не изменилось во время загрузки."""

        try:
            result = await self.db_session.execute(
                update(LegalChange)
                .where(
                    LegalChange.id == change.id,
                    LegalChange.updated_at == change.updated_at,
                    LegalChange.status.in_([
                        LegalChangeStatus.DRAFT, LegalChangeStatus.APPROVED,
                        LegalChangeStatus.SCHEDULED, LegalChangeStatus.FAILED,
                    ]),
                )
                .values(consolidated_text=extracted_text, consolidated_text_source=source)
                .returning(LegalChange.id)
                .execution_options(synchronize_session=False)
            )
            saved = result.scalar_one_or_none() is not None
            await self.db_session.commit()
            return saved
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise LegalChangeRepositoryError(str(error)) from error

    async def update(self, change: LegalChange, values: dict) -> LegalChange:
        """Обновляет событие изменения."""

        for field, value in values.items():
            setattr(change, field, value)

        try:
            await self.db_session.commit()
            await self.db_session.refresh(change)
            return change
        except SQLAlchemyError as error:
            await self.db_session.rollback()
            raise LegalChangeRepositoryError(str(error)) from error
