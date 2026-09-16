from urllib.parse import unquote

from app.admin.documentation import REPOSITORY_URL, load_documentation, render_documentation
from bs4 import BeautifulSoup


def test_real_readme_renders_tables_code_diagram_and_resolvable_navigation():
    document = load_documentation()
    soup = BeautifulSoup(str(document.html), "html.parser")
    ids = [node["id"] for node in soup.select("[id]")]
    assert len(ids) == len(set(ids))
    assert soup.select("table")
    assert soup.select("pre code.language-mermaid")
    assert soup.select("pre code.language-json span")
    for section in document.sections:
        assert section.anchor in ids
    for link in soup.select('a[href^="#"]'):
        assert unquote(link["href"][1:]) in {*ids, "documentation-top"}
    assert soup.find("a", href=REPOSITORY_URL + "/blob/main/app/services/monitoring.py")
    assert soup.find("a", href=REPOSITORY_URL + "/tree/main/app/schemas")


def test_markdown_cannot_execute_html_or_escape_documentation_anchors():
    source = '''<script>alert(1)</script>
<iframe src="https://example.com"></iframe>
<form action="/admin/logout"><input name="secret"></form>
<a id="navbarSupportedContent" onclick="alert(1)">Anchor</a>
<a href="javascript:alert(1)">Unsafe link</a>

## Повтор
## Повтор

[К якорю](#navbarSupportedContent)
[В этот README](README.md#navbarSupportedContent)
[Код](app/main.py)

```html
<script>alert("literal code")</script>
```
'''
    soup = BeautifulSoup(str(render_documentation(source).html), "html.parser")
    assert not soup.select("script, iframe, form, input, [onclick]")
    assert not soup.find("a", href="javascript:alert(1)")
    assert soup.find(id="navbarSupportedContent") is None
    assert soup.find(id="readme-navbarSupportedContent") is not None
    assert len(soup.find_all("a", href="#readme-navbarSupportedContent")) == 2
    assert soup.find(id="readme-повтор") and soup.find(id="readme-повтор-1")
    assert '<script>alert("literal code")</script>' in soup.select_one("pre").get_text()
    code_link = soup.find("a", href=REPOSITORY_URL + "/blob/main/app/main.py")
    assert code_link["target"] == "_blank"
    assert "noopener" in code_link["rel"]
