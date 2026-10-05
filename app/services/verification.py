"""Проверяемые правила и привязка решения к точным входным данным."""
import hashlib
import json
import re
from datetime import date

from bs4 import BeautifulSoup

from app.clients.verification import VerificationInvalid


def normalized(value: str) -> str:
    text = " ".join(value.replace("\u200b", "").split())
    return re.sub(r"\s+([,.;:?!])", r"\1", text)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def json_digest(value: dict) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str))


def official_plain_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    # Портал обозначает надстрочные номера как <sup> или CSS-классом
    # (например, W9). Имя класса зависит от документа; читаем его объявление.
    superscript_classes = {
        match[1] for style in soup.select("style")
        for match in re.finditer(r"\.([\w-]+)\s*\{([^{}]*)\}", style.get_text())
        if re.search(r"vertical-align\s*:\s*super\b", match[2], re.IGNORECASE)
    }
    for tag in soup.select("sup, span"):
        superscript = (tag.name == "sup" or superscript_classes.intersection(tag.get("class", []))
                       or re.search(r"vertical-align\s*:\s*super\b", tag.get("style", ""), re.IGNORECASE))
        if superscript and tag.get_text(strip=True).isdigit():
            tag.replace_with("." + tag.get_text(strip=True))
    for tag in soup.select("script, style"):
        tag.decompose()
    text = re.sub(r"(?<=\d)\s*\.\s*(?=\d)", ".", normalized((soup.body or soup).get_text(" ", strip=True)))
    return re.sub(r"\s+([,.;:])", r"\1", text)


def payload_identity(change) -> dict:
    doc = change.tracked_document
    return {
        "event_id": change.id, "document_id": doc.document_id, "source_hash": doc.ebpi_doc_hash,
        "section_number": change.section_number, "section_title": change.section_title or doc.short_name,
        "redaction_id": change.ebpi_redaction_id, "revision_date": str(change.redaction_date),
        "effective_date": str(change.effective_date), "send_at": str(change.send_at),
        "amending_hash": change.amending_doc_hash, "amending_ref": change.amending_law_ref,
        "act_type": change.amending_act_type, "act_number": change.amending_act_number,
        "act_date": str(change.amending_act_date), "category": doc.category,
        "audience": change.audience_override or doc.audience,
        "source_title": change.source_title_override or doc.source_title,
        "topics": change.topics_override if change.topics_override is not None else doc.topics,
    }


def compare_all_articles(previous, current) -> list[dict]:
    """Сравнивает ВСЕ статьи, независимо от ссылок на закон в разметке портала."""
    old = {s.number for s in previous.sections}
    new = {s.number for s in current.sections}
    if previous._invalid_sections or current._invalid_sections or previous.unparsed_article_captions or current.unparsed_article_captions:
        raise VerificationInvalid("В оглавлении есть статьи с неоднозначными границами.")
    result = []
    for number in sorted(old | new, key=lambda s: tuple(int(p) for p in s.split("."))):
        before = previous.extract_section_text(number) if number in old else ""
        after = current.extract_section_text(number) if number in new else ""
        if normalized(before) != normalized(after):
            result.append({"section_number": number, "before": before, "after": after,
                           "operation": "added" if number not in old else "removed" if number not in new else "changed"})
    return result


MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


def date_is_cited(value: date, quote: str) -> bool:
    """Авторежим v1 допускает только явно указанную абсолютную дату."""
    quote = normalized(quote).lower()
    numeric = rf"(?<!\d)0?{value.day}[./]0?{value.month}[./]{value.year}(?!\d)"
    words = rf"(?<!\d)0?{value.day}\s+{MONTHS[value.month - 1]}\s+{value.year}(?!\d)"
    return bool(re.search(numeric, quote) or re.search(words, quote) or value.isoformat() in quote)


def validate_report(snapshot: dict, report) -> list[str]:
    problems = []
    law = normalized(snapshot["law"]["text"])
    articles = {a["section_number"]: a for a in snapshot["articles"]}
    events = {a["section_number"]: a for a in snapshot["events"]}
    if len(events) != len(snapshot["events"]):
        problems.append("Обнаружены дубли событий по одной статье.")
    if set(articles) != set(events):
        problems.append(f"Полный diff редакций не совпадает с событиями. Пропущены: {sorted(set(articles) - set(events))}; лишние: {sorted(set(events) - set(articles))}.")
    active = [item for item in report.expected_articles if item.applies_to_revision]
    numbers = [item.section_number for item in active]
    if len(numbers) != len(set(numbers)):
        problems.append("LLM указала повторные статьи для одной даты.")
    if set(numbers) != set(articles) or set(numbers) != set(events):
        problems.append("Перечень статей из закона не совпадает с полным diff и событиями.")
    if not active:
        problems.append("Нет подтверждённых поправок для этой редакции.")
    for item in report.expected_articles:
        label = f"Статья {item.section_number}"
        if normalized(item.law_quote) not in law or normalized(item.date_quote) not in law:
            problems.append(f"{label}: цитата не найдена в официальном законе.")
        number = re.escape(item.section_number)
        if not re.search(rf"стать[а-я]*\s+{number}(?![\d.])", normalized(item.law_quote), re.IGNORECASE):
            problems.append(f"{label}: цитата не содержит прямого указания номера статьи.")
        if item.effective_date is None or not date_is_cited(item.effective_date, item.date_quote):
            problems.append(f"{label}: дата не подтверждена явной датой в законе.")
        is_current = str(item.effective_date) == snapshot["revision"]["date"]
        if is_current != item.applies_to_revision:
            problems.append(f"{label}: противоречие в отнесении поправки к редакции.")
        if item.applies_to_revision:
            article, event = articles.get(item.section_number), events.get(item.section_number)
            if item.assessment != "matches":
                problems.append(f"{label}: {item.explanation}")
            if event and event["effective_date"] != str(item.effective_date):
                problems.append(f"{label}: дата события расходится с законом.")
            if article and (not article["after"] or (article["operation"] == "added") != (item.operation == "added")):
                problems.append(f"{label}: добавление/удаление статьи требует отдельного разбора.")
    if report.verdict != "pass" or report.issues or report.unsupported_provisions:
        problems.extend(report.issues + report.unsupported_provisions or [report.summary])
    return list(dict.fromkeys(problems))
