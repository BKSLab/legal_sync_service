import pytest
from app.exceptions.redaction import RedactionParseError, RedactionSectionNotFoundError
from app.services.redaction_parser import RedactionDocument
from tests.conftest import FZ_246_CITATION, FZ_246_DOC_HASH


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
