import pytest
from app.exceptions.redaction import RedactionParseError, RedactionSectionNotFoundError
from app.schemas.pravo_ebpi import EbpiContentNode
from app.services.redaction_parser import RedactionDocument
from tests.conftest import FZ_246_CITATION, FZ_246_DOC_HASH, FZ_651_DOC_HASH


@pytest.fixture
def document(redaction_html, redaction_content_nodes) -> RedactionDocument:
    return RedactionDocument(
        redaction_html=redaction_html,
        content_nodes=redaction_content_nodes,
    )


def test_sections_are_built_from_official_content(document):
    """Границы статей берутся из оглавления портала, а не из разбора заголовков."""

    assert [section.number for section in document.sections] == [
        "57",
        "58",
        "59",
        "60",
        "60.1",
        "60.2",
    ]


def test_superscript_article_number_is_normalized(document):
    """«Статья 60<sup>1</sup>» — это статья 60.1, а не 60 и не 601."""

    section = document.get_section(section_number="60.1")
    assert section.title == "Работа по совместительству"


def test_finds_amending_document_hash_by_citation(document):
    assert document.find_amending_document_hash(citation=FZ_246_CITATION) == FZ_246_DOC_HASH


def test_unknown_citation_gives_none_instead_of_wrong_hash(document):
    assert document.find_amending_document_hash(citation="от 01.01.2000 № 1-ФЗ") is None


def test_finds_sections_changed_by_amending_law(document):
    """ФЗ № 246-ФЗ от 26.07.2026 меняет в этом фрагменте статьи 57, 58 и 59."""

    changed = document.find_sections_changed_by(amending_document_hash=FZ_246_DOC_HASH)

    assert [section.number for section in changed] == ["57", "58", "59"]


def test_unrelated_law_changes_nothing(document):
    changed = document.find_sections_changed_by(amending_document_hash="0" * 64)

    assert changed == []


def test_extracts_full_section_text(document):
    text = document.extract_section_text(section_number="59")

    assert text.startswith("Статья 59. Срочный трудовой договор")
    assert "Срочный трудовой договор заключается:" in text
    # Текст статьи длинный: обрезанный фрагмент отправлять в RAG нельзя.
    assert len(text) > 4000


def test_section_text_does_not_leak_into_next_section(document):
    text = document.extract_section_text(section_number="60")

    assert text.startswith("Статья 60.")
    assert "Статья 60 1 ." not in text


def test_missing_section_raises(document):
    with pytest.raises(RedactionSectionNotFoundError):
        document.extract_section_text(section_number="999")


def test_broken_markup_raises_instead_of_partial_parse(redaction_content_nodes):
    """Изменение разметки портала должно давать явную ошибку, а не пустой текст."""

    with pytest.raises(RedactionParseError):
        RedactionDocument(
            redaction_html="<html><body><div>без абзацев</div></body></html>",
            content_nodes=redaction_content_nodes,
        )


def test_staged_amendment_excludes_unchanged_article_with_old_marker(fz181_redaction_data):
    old_html, old_nodes = fz181_redaction_data[486059]
    new_html, new_nodes = fz181_redaction_data[444605]
    previous = RedactionDocument(old_html, old_nodes)
    current = RedactionDocument(new_html, new_nodes)

    assert [section.number for section in current.find_sections_changed_by(FZ_651_DOC_HASH)] == ["1", "14"]
    changed = current.find_sections_changed_by(FZ_651_DOC_HASH, previous_redaction=previous)

    assert [section.number for section in changed] == ["14"]


def test_unchanged_redaction_creates_no_article_changes(fz181_redaction_data):
    html, nodes = fz181_redaction_data[444605]
    previous = RedactionDocument(html, nodes)
    current = RedactionDocument(html, nodes)

    assert current.find_sections_changed_by(FZ_651_DOC_HASH, previous_redaction=previous) == []


def test_comparison_ignores_whitespace_changes(fz181_redaction_data):
    html, nodes = fz181_redaction_data[444605]
    formatted_html = html.replace("Понятия инвалида", "Понятия \u00a0  инвалида")
    assert formatted_html != html
    previous = RedactionDocument(formatted_html, nodes)
    current = RedactionDocument(html, nodes)

    assert current.find_sections_changed_by(FZ_651_DOC_HASH, previous_redaction=previous) == []


def test_articles_missing_from_previous_redaction_are_new(fz181_redaction_data):
    html, nodes = fz181_redaction_data[444605]
    previous = RedactionDocument(
        '<p id="old">Статья 3. Общие положения</p>',
        [EbpiContentNode.model_validate({
            "id": "old", "caption": "Статья 3. Общие положения", "unit": "статья",
            "np": "old", "npe": "old",
        })],
    )
    current = RedactionDocument(html, nodes)

    changed = current.find_sections_changed_by(FZ_651_DOC_HASH, previous_redaction=previous)

    assert [section.number for section in changed] == ["1", "14"]


def test_missing_end_uses_next_official_boundary_without_including_next_article():
    document = RedactionDocument(
        '<p id="start">Статья 1. Название</p><p id="body">Текст первой статьи.</p>'
        '<p id="next">Статья 2. Другая статья</p><p id="end">Текст второй статьи.</p>',
        [EbpiContentNode.model_validate(node) for node in [
            {"id": "1", "caption": "Статья 1. Название", "unit": "статья", "lvl": 1, "np": "start", "npe": "missing"},
            {"id": "2", "caption": "Статья 2. Другая статья", "unit": "статья", "lvl": 1, "np": "next", "npe": "end"},
        ]],
    )

    assert document.extract_section_text("1") == "Статья 1. Название\nТекст первой статьи."


def test_unreadable_previous_article_is_not_treated_as_new(fz181_redaction_data):
    html, nodes = fz181_redaction_data[444605]
    broken_nodes = [nodes[0].model_copy(update={"first_paragraph_id": "missing"}), nodes[1]]
    previous = RedactionDocument(html, broken_nodes)
    current = RedactionDocument(html, nodes)

    with pytest.raises(RedactionParseError, match="границы статьи 1"):
        current.find_sections_changed_by(FZ_651_DOC_HASH, previous_redaction=previous)
