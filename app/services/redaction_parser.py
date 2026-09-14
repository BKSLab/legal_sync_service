import bisect
import logging
import re

from bs4 import BeautifulSoup, Tag

from app.exceptions.redaction import (
    RedactionParseError,
    RedactionSectionNotFoundError,
)
from app.schemas.pravo_ebpi import EbpiContentNode
from app.schemas.redaction import RedactionSection

logger = logging.getLogger(__name__)


class RedactionDocument:
    """Разобранная редакция правового акта.

    Объединяет два ответа портала по одной редакции: HTML полного текста и
    структурное оглавление. Границы статей берутся из оглавления, а не
    вычисляются по заголовкам, поэтому извлечение текста статьи не зависит от
    того, как именно оформлен её заголовок.

    Разбор выполняется в конструкторе и для кодекса занимает заметное время
    (ТК РФ — около 1,3 млн символов HTML), поэтому объект строится один раз на
    редакцию и переиспользуется для всех её статей.
    """

    ARTICLE_UNIT = "статья"
    # Заголовок статьи в оглавлении: «Статья 60<sup>1</sup>. Работа по ...».
    ARTICLE_CAPTION_PATTERN = re.compile(r"^\s*Статья\s+(?P<number>\d+)\s*(?P<index>\d+)?\s*\.?\s*(?P<title>.*)$", re.DOTALL)
    CHANGE_LINK_CLASS = "cmd"
    CHANGE_LINK_PARAM_ATTRIBUTE = "cmdprm"
    CHANGE_LINK_HASH_PREFIX = "gohash="
    CHANGE_LINK_HASH_PATTERN = re.compile(r"gohash=([0-9a-f]{64})")

    def __init__(self, redaction_html: str, content_nodes: list[EbpiContentNode]):
        self.soup = BeautifulSoup(redaction_html, "lxml")
        self.paragraphs = self._collect_paragraphs()
        self.paragraph_index = {
            paragraph_id: position
            for position, (paragraph_id, _) in enumerate(self.paragraphs)
            if paragraph_id
        }
        self.sections = self._collect_sections(content_nodes=content_nodes)
        self._section_starts = [section.first_paragraph_position for section in self.sections]
        logger.info(
            "✅ Редакция разобрана. абзацев=%s статей=%s",
            len(self.paragraphs),
            len(self.sections),
        )

    # Блок публичных методов

    def find_sections_changed_by(self, amending_document_hash: str) -> list[RedactionSection]:
        """Находит статьи, изменённые конкретным актом-поправкой.

        Портал помечает каждый изменённый абзац ссылкой на акт-поправку с его
        идентификатором `gohash`. Сопоставление идёт только по этому
        идентификатору: номер закона повторяется в разные годы, поэтому по
        номеру статьи определяются неверно.

        Args:
            amending_document_hash: Идентификатор акта-поправки в банке редакций.

        Returns:
            Список изменённых статей в порядке следования в документе.
        """

        changed_positions: set[int] = set()
        for position, (_, paragraph) in enumerate(self.paragraphs):
            if self._is_changed_by(paragraph=paragraph, document_hash=amending_document_hash):
                changed_positions.add(position)

        changed_sections: dict[str, RedactionSection] = {}
        first_section_start = self._section_starts[0]
        for position in sorted(changed_positions):
            section = self._section_at(position=position)
            if section is None:
                if position < first_section_start:
                    # Преамбула документа перечисляет все изменявшие его акты,
                    # поэтому ссылка на акт-поправку там есть всегда. Это не
                    # изменение статьи и не повод для предупреждения.
                    logger.debug(
                        "Ссылка на акт-поправку в преамбуле пропущена. позиция=%s",
                        position,
                    )
                else:
                    logger.warning(
                        "⚠️ Изменённый абзац вне границ статьи. позиция=%s hash=%s",
                        position,
                        amending_document_hash,
                    )
                continue
            changed_sections.setdefault(section.number, section)

        logger.info(
            "✅ Изменённых статей найдено: %s. hash=%s",
            len(changed_sections),
            amending_document_hash,
        )
        return list(changed_sections.values())

    def find_amending_document_hash(self, citation: str) -> str | None:
        """Находит идентификатор акта-поправки по его реквизитам в тексте редакции.

        Список редакций отдаёт только подпись вида `(№ 246-ФЗ от 26.07.2026)`,
        а привязка изменений к статьям работает по идентификатору акта. Ссылки
        на акты в тексте редакции содержат и то, и другое, поэтому реквизиты
        переводятся в идентификатор по самому же тексту редакции.

        Сопоставление идёт по паре «дата плюс номер»: одного номера
        недостаточно, он повторяется в разные годы.

        Args:
            citation: Реквизиты акта в виде `от 26.07.2026 № 246-ФЗ`.

        Returns:
            Идентификатор акта-поправки или `None`, если ссылка не найдена.
        """

        expected = self._normalize_whitespace(citation)
        for link in self.soup.find_all("span", class_=self.CHANGE_LINK_CLASS):
            if self._normalize_whitespace(link.get_text(" ", strip=True)) != expected:
                continue
            parameters = link.get(self.CHANGE_LINK_PARAM_ATTRIBUTE) or ""
            match = self.CHANGE_LINK_HASH_PATTERN.search(parameters)
            if match:
                return match.group(1)
        return None

    def extract_section_text(self, section_number: str) -> str:
        """Извлекает полный текст статьи из редакции.

        Текст не переформулируется и не сокращается: абзацы берутся как есть,
        нормализуются только пробельные символы.

        Args:
            section_number: Номер статьи, например `59` или `60.1`.

        Returns:
            Текст статьи, по одному абзацу на строку.

        Raises:
            RedactionSectionNotFoundError: Статьи нет в этой редакции.
        """

        section = self.get_section(section_number=section_number)
        lines = []
        for _, paragraph in self.paragraphs[
            section.first_paragraph_position : section.last_paragraph_position + 1
        ]:
            line = self._normalize_whitespace(paragraph.get_text(" ", strip=True))
            if line:
                lines.append(line)
        if not lines:
            raise RedactionSectionNotFoundError(section_number=section_number)
        return "\n".join(lines)

    def get_section(self, section_number: str) -> RedactionSection:
        """Возвращает описание статьи по её номеру.

        Args:
            section_number: Номер статьи.

        Returns:
            Описание статьи с границами абзацев.

        Raises:
            RedactionSectionNotFoundError: Статьи нет в этой редакции.
        """

        for section in self.sections:
            if section.number == section_number:
                return section
        raise RedactionSectionNotFoundError(section_number=section_number)

    # Блок приватных методов разбора

    def _collect_paragraphs(self) -> list[tuple[str | None, Tag]]:
        """Собирает абзацы редакции в порядке следования в документе."""

        root = self.soup.body or self.soup
        paragraphs = [(paragraph.get("id"), paragraph) for paragraph in root.find_all("p")]
        if not paragraphs:
            raise RedactionParseError("В HTML редакции не найдено ни одного абзаца.")
        return paragraphs

    def _collect_sections(self, content_nodes: list[EbpiContentNode]) -> list[RedactionSection]:
        """Строит список статей по структурному оглавлению редакции."""

        sections: list[RedactionSection] = []
        for node in content_nodes:
            if node.unit != self.ARTICLE_UNIT:
                continue
            parsed = self._parse_article_caption(caption=node.caption)
            if parsed is None:
                logger.warning("⚠️ Заголовок статьи не разобран. caption=%s", node.caption)
                continue
            number, title = parsed
            first = self.paragraph_index.get(node.first_paragraph_id or "")
            last = self.paragraph_index.get(node.last_paragraph_id or "")
            if first is None or last is None or last < first:
                logger.warning(
                    "⚠️ Границы статьи не найдены в HTML. number=%s np=%s npe=%s",
                    number,
                    node.first_paragraph_id,
                    node.last_paragraph_id,
                )
                continue
            sections.append(
                RedactionSection(
                    number=number,
                    title=title,
                    first_paragraph_position=first,
                    last_paragraph_position=last,
                )
            )
        if not sections:
            raise RedactionParseError("В оглавлении редакции не найдено ни одной статьи.")
        sections.sort(key=lambda section: section.first_paragraph_position)
        return sections

    def _parse_article_caption(self, caption: str) -> tuple[str, str] | None:
        """Разбирает заголовок статьи из оглавления на номер и название.

        Номер с надстрочным индексом (`Статья 60<sup>1</sup>`) приводится к
        виду `60.1` — портал так обозначает статьи, добавленные между
        существующими.
        """

        plain_caption = BeautifulSoup(caption, "lxml").get_text(" ", strip=False)
        match = self.ARTICLE_CAPTION_PATTERN.match(plain_caption)
        if match is None:
            return None
        number = match.group("number")
        if match.group("index"):
            number = f"{number}.{match.group('index')}"
        title = self._normalize_whitespace(match.group("title")).lstrip(". ").strip()
        return number, title

    def _is_changed_by(self, paragraph: Tag, document_hash: str) -> bool:
        """Проверяет, помечен ли абзац как изменённый указанным актом."""

        marker = f"{self.CHANGE_LINK_HASH_PREFIX}{document_hash}"
        for link in paragraph.find_all("span", class_=self.CHANGE_LINK_CLASS):
            if marker in (link.get(self.CHANGE_LINK_PARAM_ATTRIBUTE) or ""):
                return True
        return False

    def _section_at(self, position: int) -> RedactionSection | None:
        """Возвращает статью, в границы которой попадает абзац."""

        candidate = bisect.bisect_right(self._section_starts, position) - 1
        if candidate < 0:
            return None
        section = self.sections[candidate]
        if position > section.last_paragraph_position:
            return None
        return section

    @staticmethod
    def _normalize_whitespace(value: str) -> str:
        """Схлопывает пробельные символы, включая неразрывные пробелы портала."""

        return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()
