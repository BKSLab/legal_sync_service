from datetime import date
from unittest.mock import AsyncMock

from app.clients.pravo_ebpi import PravoEbpiClient
from app.schemas.pravo_ebpi import EbpiContentNode, EbpiDocumentCard, EbpiRedaction
from app.schemas.verification import VerificationReport

DOC_HASH, LAW_HASH = "a" * 64, "b" * 64
DATE_QUOTE = "Настоящий Федеральный закон вступает в силу с 1 марта 2027 года."
QUOTES = {"57": 'В статье 57 слово «Старое» заменить словом «Новое».', "59": 'В статье 59 слово «Старое» заменить словом «Новое».'}
LAW = "Федеральный закон от 26.07.2026 № 246-ФЗ. " + " ".join(QUOTES.values()) + " " + DATE_QUOTE


def report():
    return VerificationReport.model_validate({
        "verdict": "pass", "summary": "Обе поправки и дата подтверждены.", "unsupported_provisions": [], "issues": [],
        "expected_articles": [dict(section_number=n, effective_date="2027-03-01", applies_to_revision=True,
                                   operation="changed", law_quote=q, date_quote=DATE_QUOTE,
                                   assessment="matches", explanation="Замена соответствует закону.") for n, q in QUOTES.items()],
    })


def html(word):
    return "<html><body>" + "".join(f'<p id="h{n}">Статья {n}. Условия</p><p id="t{n}">{word} условие.</p>' for n in QUOTES) + "</body></html>"


def pravo():
    client = AsyncMock(spec=PravoEbpiClient)
    current = EbpiRedaction(redaction_id=2, redaction_date=date(2027,3,1), is_completed=True, is_initial=False)
    previous = EbpiRedaction(redaction_id=1, redaction_date=date(2026,1,1), is_completed=True, is_initial=True)
    law = EbpiRedaction(redaction_id=9, redaction_date=date(2027,3,1), is_completed=True, is_initial=True)
    client.get_redactions.side_effect = lambda document_hash: [law] if document_hash == LAW_HASH else [current, previous]
    law_card = EbpiDocumentCard(
        doc_id=20, doc_hash=LAW_HASH, doc_passing="Федеральный закон от 26.07.2026 № 246-ФЗ", doc_name="О поправках",
        adoptions=[{"type":"Федеральный закон","onumber":"246-ФЗ","odate":"26.07.2026"}],
    )
    document_card = EbpiDocumentCard(doc_id=10, doc_hash=DOC_HASH, doc_name="Тестовый кодекс",
                                   adoptions=[{"type":"Кодекс","onumber":"197-ФЗ","odate":"30.12.2001"}])
    client.get_document_card_by_hash.side_effect = lambda document_hash: law_card if document_hash == LAW_HASH else document_card
    client.get_redaction_text.side_effect = lambda redaction_id: "<p>" + LAW + "</p>" if redaction_id == 9 else html("Новое" if redaction_id == 2 else "Старое")
    client.get_redaction_content.return_value = [EbpiContentNode(
        node_id=n, caption=f"Статья {n}. Условия", unit="статья", level=1, first_paragraph_id="h"+n, last_paragraph_id="t"+n,
    ) for n in QUOTES]
    return client
