from app.admin import documentation
from bs4 import BeautifulSoup


async def test_documentation_and_original_download_require_login_and_follow_readme_changes(
    admin_client, monkeypatch, tmp_path,
):
    readme = tmp_path / "README.md"
    source = "# Справка\r\n\r\n## Раздел\r\nТекущий текст README.\r\n".encode()
    readme.write_bytes(source)
    monkeypatch.setattr(documentation, "README_PATH", readme)
    for path in ("/admin/documentation", "/admin/documentation/source"):
        response = await admin_client.get(path)
        assert response.status_code == 302
        assert response.headers["location"].endswith("/admin/login")
    await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    response = await admin_client.get("/admin/documentation")
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, "html.parser")
    assert soup.select_one(".documentation-article h1").get_text() == "Справка"
    menu = soup.select_one('.navbar-nav a[href$="/documentation"]')
    assert menu and "Документация" in menu.get_text()
    assert soup.select_one('.documentation-navigation a[href="#readme-раздел"]')
    download = await admin_client.get("/admin/documentation/source?path=.env")
    assert download.content == source
    assert 'attachment; filename="README.md"' == download.headers["content-disposition"]
    readme.write_text("# Изменённый README\nНовая версия без перезапуска.", encoding="utf-8")
    response = await admin_client.get("/admin/documentation")
    assert "Новая версия без перезапуска." in response.text
    assert "Текущий текст README." not in response.text


async def test_missing_readme_returns_explanation_and_does_not_reuse_old_content(
    admin_client, monkeypatch, tmp_path,
):
    monkeypatch.setattr(documentation, "README_PATH", tmp_path / "missing.md")
    await admin_client.post("/admin/login", data={"username": "operator", "password": "test-admin-password"})
    response = await admin_client.get("/admin/documentation")
    assert response.status_code == 503
    assert "Не удалось прочитать README.md" in response.text
    assert (await admin_client.get("/admin/documentation/source")).status_code == 503
