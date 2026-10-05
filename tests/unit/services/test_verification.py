from copy import deepcopy
from datetime import date
from types import SimpleNamespace

import pytest
from app.services.automation import AutomationService
from app.services.verification import normalized, official_plain_text, validate_report
from tests.automation_fixtures import DOC_HASH, LAW_HASH, pravo, report


async def snapshot():
    changes = [SimpleNamespace(id=i, section_number=n, redaction_date=date(2027,3,1), effective_date=date(2027,3,1),
                               amending_doc_hash=LAW_HASH, amending_act_number="246-ФЗ", amending_act_date=date(2026,7,26),
                               amending_act_type="Федеральный закон", amending_law_ref="от 26.07.2026 № 246-ФЗ") for i,n in enumerate(["57","59"],1)]
    document = SimpleNamespace(is_active=True, document_id="test-code", full_title="Тестовый кодекс", ebpi_doc_hash=DOC_HASH,
                               document_number="197-ФЗ", adoption_date=date(2001,12,30))
    case = SimpleNamespace(amending_doc_hash=LAW_HASH, redaction_id=2)
    return await AutomationService(None, pravo(), None, None)._snapshot(case, document, changes)


async def test_all_articles_are_compared_even_without_amendment_markers():
    data = await snapshot()
    assert [a["section_number"] for a in data["articles"]] == ["57", "59"]
    assert validate_report(data, report()) == []


@pytest.mark.parametrize("failure", ["missing_event", "extra_event", "wrong_date", "fabricated_quote", "wrong_article_quote", "truncated_coverage", "unknown", "transition", "wrong_phase", "missing_date", "duplicate", "deleted"])
async def test_invalid_evidence_never_passes(failure):
    data, response = await snapshot(), report()
    if failure == "missing_event":
        data["events"].pop()
    elif failure == "extra_event":
        data["events"].append({"id":3,"section_number":"60","effective_date":"2027-03-01"})
    elif failure == "wrong_date":
        data["events"][0]["effective_date"] = "2026-01-01"
    elif failure == "fabricated_quote":
        response.expected_articles[0].law_quote = "Нет такого текста в законе"
    elif failure == "wrong_article_quote":
        response.expected_articles[0].law_quote = response.expected_articles[1].law_quote
    elif failure == "truncated_coverage":
        response.expected_articles.pop()
    elif failure == "unknown":
        response.expected_articles[0].assessment = "unknown"
    elif failure == "transition":
        response.unsupported_provisions = ["Особый переходный режим"]
    elif failure == "wrong_phase":
        response.expected_articles[0].applies_to_revision = False
    elif failure == "missing_date":
        response.expected_articles[0].effective_date = None
    elif failure == "duplicate":
        response.expected_articles.append(deepcopy(response.expected_articles[0]))
    elif failure == "deleted":
        data["articles"][0]["after"] = ""
    assert validate_report(data, response)


def test_superscript_in_amending_act_is_preserved_as_article_number():
    assert official_plain_text('<p>Статья 351<sup>9</sup>. Текст</p>') == "Статья 351.9. Текст"
    assert official_plain_text('<style>.W9{vertical-align:super; font-size:100%;}</style><p>Статья 351<span class="W9">9</span>. Текст</p>') == "Статья 351.9. Текст"
    assert official_plain_text('<p>Статья 351<span style="vertical-align: super">9</span>. Текст</p>') == "Статья 351.9. Текст"


def test_html_spacing_before_punctuation_is_not_a_legal_change():
    assert normalized("инфраструктур , объектов") == normalized("инфраструктур, объектов")
    assert normalized("инфраструктур, объектов") != normalized("инфраструктур объектов")


def test_unparsed_article_cannot_be_silently_excluded_from_full_diff(redaction_html, redaction_content_nodes):
    from app.clients.verification import VerificationInvalid
    from app.services.redaction_parser import RedactionDocument
    from app.services.verification import compare_all_articles
    original = RedactionDocument(redaction_html, redaction_content_nodes)
    nodes = [node.model_copy(update={'caption': 'Неразобранный заголовок'}) if node.unit == 'статья' and i == next(j for j,n in enumerate(redaction_content_nodes) if n.unit == 'статья') else node for i,node in enumerate(redaction_content_nodes)]
    changed = RedactionDocument(redaction_html, nodes)
    with pytest.raises(VerificationInvalid):
        compare_all_articles(original, changed)
