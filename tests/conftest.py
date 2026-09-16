import json
from pathlib import Path

import pytest
from app.schemas.pravo_ebpi import EbpiContentNode, EbpiRedaction

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "pravo_ebpi"

# Идентификатор ФЗ от 26.07.2026 № 246-ФЗ в банке редакций. Тот же акт
# опубликован на publication.pravo.gov.ru под eoNumber 0001202607260006.
FZ_246_DOC_HASH = "797105206ac17902a9990082c79054b4f646947a5415d712d44ff90bf4ce8dd0"
FZ_246_CITATION = "от 26.07.2026 № 246-ФЗ"
FZ_651_DOC_HASH = "3c787db9a2a18cbd54a8261100b1e1b21269370fbaf54b7c7d0ccb3ecc5802df"
TK_RF_DOC_HASH = "03bdee8f44a71247d7ba3342484326dbc3fa292d3caae9f88ada4b1ee38c20d9"
TK_RF_REDACTION_ID = 495396


def _load_json(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def redaction_html() -> str:
    """HTML фрагмента редакции ТК РФ на 01.03.2027 со статьями 57-60.2."""

    return (FIXTURES_DIR / "redtext_tk_rf.html").read_text(encoding="utf-8")


@pytest.fixture
def redaction_content_nodes() -> list[EbpiContentNode]:
    """Структурное оглавление того же фрагмента редакции ТК РФ."""

    payload = _load_json("getcontent_tk_rf.json")
    return [EbpiContentNode.model_validate(node) for node in payload["data"]]


@pytest.fixture
def fz181_redaction_data() -> dict[int, tuple[str, list[EbpiContentNode]]]:
    """Живые фрагменты статей 1 и 14: старая отметка и новый этап ФЗ № 651-ФЗ."""

    result = {}
    for redaction_id, suffix in [(486059, "20260901"), (444605, "20270901")]:
        html = (FIXTURES_DIR / f"redtext_fz181_{suffix}.html").read_text(encoding="utf-8")
        content = _load_json(f"getcontent_fz181_{suffix}.json")
        result[redaction_id] = html, [EbpiContentNode.model_validate(node) for node in content["data"]]
    return result


@pytest.fixture
def redactions_payload() -> dict:
    """Ответ портала со списком редакций ТК РФ."""

    return _load_json("redactions_tk_rf.json")


@pytest.fixture
def redactions() -> list[EbpiRedaction]:
    """Разобранный список редакций ТК РФ."""

    payload = _load_json("redactions_tk_rf.json")
    return [EbpiRedaction.model_validate(item) for item in payload["redactions"]]


@pytest.fixture
def search_payload() -> dict:
    """Ответ портала на поиск ТК РФ по реквизитам."""

    return _load_json("attrsearch_tk_rf.json")


@pytest.fixture
def card_payload() -> dict:
    """Карточка ФЗ от 26.07.2026 № 246-ФЗ по номеру опубликования."""

    return _load_json("card_fz_246.json")
