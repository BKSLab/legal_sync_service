"""README проекта как документация внутри авторизованной админки."""

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import nh3
from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from markupsafe import Markup
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound
from sqladmin import BaseView, expose
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
README_PATH = PROJECT_ROOT / "README.md"
REPOSITORY_URL = "https://github.com/BKSLab/legal_sync_service"
ANCHOR_PREFIX = "readme-"


@dataclass(frozen=True)
class DocumentationSection:
    anchor: str
    title: str


@dataclass(frozen=True)
class RenderedDocumentation:
    html: Markup
    sections: tuple[DocumentationSection, ...]


def _repository_link(href: str) -> str:
    """Фрагменты остаются на странице; исходники открываются в GitHub."""
    parts = urlsplit(href)
    if parts.scheme or parts.netloc:
        return href
    path = PurePosixPath(unquote(parts.path))
    fragment = f"#{ANCHOR_PREFIX}{unquote(parts.fragment)}" if parts.fragment else ""
    if not parts.path or path == PurePosixPath("README.md"):
        return fragment or "#documentation-top"
    # Никакие файлы по ссылкам из Markdown сервер не читает и не отдаёт.
    if path.is_absolute() or ".." in path.parts:
        return REPOSITORY_URL
    kind = "tree" if (PROJECT_ROOT / path).is_dir() else "blob"
    target = f"{REPOSITORY_URL}/{kind}/main/{quote(str(path), safe='/')}"
    return f"{target}#{quote(parts.fragment)}" if parts.fragment else target


@lru_cache(maxsize=2)
def render_documentation(source: str) -> RenderedDocumentation:
    """Кэш зависит от текста: изменение README видно при следующем открытии."""
    markdown = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    html = nh3.clean(
        markdown.render(source),
        tags={
            "a", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
            "strong", "em", "s", "blockquote", "pre", "code", "hr", "br",
            "table", "thead", "tbody", "tr", "th", "td", "sup", "sub",
        },
        attributes={
            "a": {"href", "title", "id"}, "code": {"class"}, "ol": {"start"},
            **{f"h{level}": {"id"} for level in range(1, 7)},
        },
        url_schemes={"http", "https", "mailto"},
    )
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.find_all(id=True):
        element["id"] = ANCHOR_PREFIX + element["id"]
    used_ids = {element["id"] for element in soup.find_all(id=True)}
    sections = []
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        title = heading.get_text(" ", strip=True)
        if not heading.get("id"):
            slug = re.sub(r"[^\w\s-]", "", title.lower()).replace(" ", "-") or "section"
            base = ANCHOR_PREFIX + slug
            anchor, suffix = base, 0
            while anchor in used_ids:
                suffix += 1
                anchor = f"{base}-{suffix}"
            heading["id"] = anchor
            used_ids.add(anchor)
        if heading.name == "h2":
            sections.append(DocumentationSection(heading["id"], title))

    for link in soup.find_all("a"):
        if link.get("href"):
            link["href"] = _repository_link(link["href"])
            if urlsplit(link["href"]).scheme in {"http", "https"}:
                link["target"] = "_blank"
                link["rel"] = "noopener noreferrer"
        if link.get("id") and not link.get_text(strip=True):
            link["class"] = "documentation-anchor"
            if link.parent.name == "p" and len(link.parent.contents) == 1:
                link.parent.unwrap()

    formatter = HtmlFormatter(nowrap=True)
    for code in soup.select("pre > code"):
        language = next((item.removeprefix("language-") for item in code.get("class", []) if item.startswith("language-")), "text")
        code.parent["data-language"] = language
        if language == "mermaid":
            continue
        try:
            lexer = get_lexer_by_name(language)
        except ClassNotFound:
            continue
        colored = BeautifulSoup(f"<code>{highlight(code.get_text(), lexer, formatter)}</code>", "html.parser")
        code.clear()
        code.extend(list(colored.code.contents))

    for table in soup.find_all("table"):
        wrapper = soup.new_tag("div", attrs={"class": "documentation-table", "tabindex": "0", "role": "region", "aria-label": "Таблица документации"})
        table.wrap(wrapper)
    return RenderedDocumentation(Markup(str(soup)), tuple(sections))


def load_documentation() -> RenderedDocumentation:
    return render_documentation(README_PATH.read_text(encoding="utf-8"))


class DocumentationView(BaseView):
    name = "Документация"
    icon = "fa-solid fa-book-open"

    @expose("/documentation", methods=["GET"])
    async def documentation(self, request: Request):
        error, document = None, None
        try:
            document = await run_in_threadpool(load_documentation)
        except (OSError, UnicodeError):
            logger.exception("Не удалось прочитать README для документации.")
            error = "Не удалось прочитать README.md. Проверьте, что файл включён в сборку приложения."
        return await self.templates.TemplateResponse(request, "documentation.html", {
            "title": self.name, "document": document, "error": error,
        }, status_code=503 if error else 200)

    @expose("/documentation/source", methods=["GET"])
    async def documentation_source(self, request: Request):
        try:
            content = await run_in_threadpool(README_PATH.read_bytes)
        except OSError:
            logger.exception("Не удалось скачать README документации.")
            return Response("README.md временно недоступен.", status_code=503, media_type="text/plain")
        return Response(content, media_type="text/markdown; charset=utf-8", headers={
            "Content-Disposition": 'attachment; filename="README.md"',
            "X-Content-Type-Options": "nosniff",
        })
