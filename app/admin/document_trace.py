from urllib.parse import urlencode, urlsplit

import httpx
from sqladmin import BaseView, expose
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from starlette.requests import Request

from app.admin.monitoring import positive_integer
from app.clients.rag import RagClient
from app.core.settings import get_settings
from app.db.models.delivery_attempt import DeliveryAttempt
from app.db.models.legal_changes import LegalChange
from app.db.models.monitoring import MonitoringDocumentCheck
from app.db.models.tracked_documents import TrackedDocument
from app.exceptions.rag import RagClientError

DELIVERY_LABELS = {
    'running': 'В работе', 'succeeded': 'Коллекция обновлена', 'warning': 'Обновлено с предупреждениями',
    'accepted': 'Получен ответ без подтверждения этапов', 'failed': 'Ошибка', 'rejected': 'Отклонено',
    'unknown': 'Исход неизвестен', 'postponed': 'Отложено', 'paused': 'Отправка приостановлена',
    'interrupted': 'Прервано', 'stalled': 'Нет сигнала',
}
DELIVERY_STAGES = {
    'preparing': 'Подготовка статьи', 'text_ready': 'Текст подготовлен',
    'waiting_rag': 'Запрос отправлен, ожидание RAG', 'response_received': 'Ответ RAG получен',
    'saved': 'Ответ сохранён в Legal Sync',
}


def rag_url(path: str, **query) -> str | None:
    base = get_settings().rag.rag_admin_base_url
    if not base or urlsplit(base).scheme not in ('http', 'https'):
        return None
    return base.rstrip('/') + path + ('?' + urlencode(query) if query else '')


class DocumentTraceView(BaseView):
    name = 'Путь документа'
    icon = 'fa-solid fa-route'

    @expose('/document-trace', methods=['GET'])
    async def document_trace(self, request: Request):
        document_id = request.query_params.get('document_id', '').strip()
        changes_page = positive_integer(request, 'changes_page')
        attempts_page = positive_integer(request, 'attempts_page')
        rag_page = positive_integer(request, 'rag_page')
        async with self._admin_ref.session_maker() as session:
            documents = (await session.scalars(select(TrackedDocument).order_by(TrackedDocument.document_id))).all()
            document = next((item for item in documents if item.document_id == document_id), None)
            checks = (await session.scalars(select(MonitoringDocumentCheck).where(
                MonitoringDocumentCheck.document_id == document_id,
            ).order_by(MonitoringDocumentCheck.id.desc()).limit(10))).all() if document_id else []
            change_filter = LegalChange.tracked_document.has(document_id=document_id)
            changes = (await session.scalars(select(LegalChange).options(selectinload(LegalChange.tracked_document)).where(
                change_filter,
            ).order_by(LegalChange.id.desc()).offset((changes_page - 1) * 25).limit(25))).all() if document_id else []
            changes_total = await session.scalar(select(func.count()).select_from(LegalChange).where(change_filter)) if document_id else 0
            attempts = (await session.scalars(select(DeliveryAttempt).where(
                DeliveryAttempt.document_id == document_id,
            ).order_by(DeliveryAttempt.started_at.desc(), DeliveryAttempt.id.desc()).offset((attempts_page - 1) * 25).limit(25))).all() if document_id else []
            attempts_total = await session.scalar(select(func.count()).select_from(DeliveryAttempt).where(
                DeliveryAttempt.document_id == document_id,
            )) if document_id else 0
            check_ids = {change.monitoring_check_id for change in changes if change.monitoring_check_id}
            change_checks = {check.id: check.run_id for check in (await session.scalars(
                select(MonitoringDocumentCheck).where(MonitoringDocumentCheck.id.in_(check_ids)),
            )).all()} if check_ids else {}
        rag_runs, rag_error = {'items': [], 'total': 0}, None
        if document_id:
            try:
                async with httpx.AsyncClient() as client:
                    rag_runs = await RagClient(client, get_settings().rag).get_ingestion_runs(document_id, rag_page)
            except RagClientError as error:
                rag_error = str(error)
        response = await self.templates.TemplateResponse(request, 'document_trace.html', {
            'title': self.name, 'document_id': document_id, 'document': document, 'documents': documents,
            'checks': checks, 'changes': changes, 'change_checks': change_checks,
            'attempts': attempts, 'attempts_total': attempts_total, 'attempts_page': attempts_page,
            'changes_total': changes_total, 'changes_page': changes_page,
            'rag_runs': rag_runs, 'rag_error': rag_error, 'rag_page': rag_page,
            'labels': DELIVERY_LABELS, 'delivery_stages': DELIVERY_STAGES, 'rag_url': rag_url,
        })
        response.headers['Cache-Control'] = 'no-store'
        return response
