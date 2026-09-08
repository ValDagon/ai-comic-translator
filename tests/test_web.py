import io
from pathlib import Path
from urllib.parse import unquote

import pytest
from PIL import Image
from service.paths import ProjectPaths
from tests.conftest import logout_session


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_landing_public_when_logged_out(api_client, monkeypatch):
    client, _, get_settings = api_client
    logout_session(client)

    def _settings():
        from dataclasses import replace

        base = get_settings()
        return replace(
            base,
            google_client_id="gid",
            google_client_secret="gsecret",
        )

    monkeypatch.setattr("api.resolve_runtime", _settings)
    monkeypatch.setattr("service.auth_deps.resolve_runtime", _settings)
    client.app.state.settings = _settings()

    r = client.get("/")
    assert r.status_code == 200
    assert "AI Comic Translator" in r.text
    assert "единым глоссарием" in r.text
    assert 'id="register"' in r.text
    assert "/auth/google" in r.text
    assert 'name="password"' not in r.text
    assert "Возможности" not in r.text


def test_logged_in_landing_hides_register(api_client):
    client, _, _ = api_client
    r = client.get("/")
    assert r.status_code == 200
    assert "AI Comic Translator" in r.text
    assert 'id="register"' not in r.text
    assert "К проектам" in r.text or "Открыть проекты" in r.text


def test_register_get_redirects_to_landing_anchor(api_client):
    client, _, _ = api_client
    logout_session(client)
    r = client.get("/register", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/#register"


def test_pricing_stub(api_client):
    client, _, _ = api_client
    logout_session(client)
    r = client.get("/pricing")
    assert r.status_code == 200
    assert "Pricing" in r.text
    assert "Скоро" in r.text
    assert "тарифы" in r.text.lower() or "Тарифы" in r.text


def test_ui_home(api_client):
    client, _, _ = api_client
    r = client.get("/app")
    assert r.status_code == 200
    assert "AI Comic Translator" in r.text
    assert "Начать перевод" in r.text
    assert "Мои проекты" in r.text


def test_ui_create_project_flow(api_client):
    client, _, _ = api_client
    r = client.post(
        "/ui/projects",
        data={},
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    project_id = unquote(loc.split("/projects/")[1].split("/ui")[0])
    assert project_id == "Новый выпуск"

    r = client.get(loc)
    assert r.status_code == 200
    assert "Название выпуска" in r.text
    assert 'value="Новый выпуск"' in r.text
    assert "Перевести выпуск" in r.text
    assert "определяется автоматически" in r.text
    assert "Пропустить extract" in r.text

    r = client.post(
        f"/projects/{project_id}/ui/name",
        data={"name": "My comic"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert 'value="My comic"' in r.text


def test_ui_upload_pages(api_client, tmp_path: Path):
    client, data_dir, _ = api_client
    r = client.post("/ui/projects", data={"name": "Up"}, follow_redirects=True)
    project_id = unquote(r.url.path.split("/projects/")[1].split("/ui")[0])

    png = tmp_path / "page.png"
    png.write_bytes(_png_bytes())
    with png.open("rb") as f:
        r = client.post(
            f"/projects/{project_id}/ui/upload",
            files=[("files", ("page.png", f, "image/png"))],
            follow_redirects=True,
        )
    assert r.status_code == 200
    assert (data_dir / "projects" / project_id / "input" / "page.png").is_file()


def test_ui_upload_font(api_client, tmp_path: Path):
    from render import DEFAULT_FONT_CANDIDATES

    font_bytes = None
    for path in DEFAULT_FONT_CANDIDATES:
        p = Path(path)
        if p.is_file():
            font_bytes = p.read_bytes()
            break
    if font_bytes is None:
        pytest.skip("Нет системного TTF")

    client, data_dir, _ = api_client
    r = client.post("/ui/projects", data={"name": "FontUp"}, follow_redirects=True)
    project_id = unquote(r.url.path.split("/projects/")[1].split("/ui")[0])

    r = client.post(
        f"/projects/{project_id}/ui/font",
        files=[("file", ("comic.ttf", font_bytes, "font/ttf"))],
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "comic.ttf" in r.text or "Шрифт" in r.text
    fonts_dir = data_dir / "projects" / project_id / "fonts"
    assert fonts_dir.is_dir()
    assert any(fonts_dir.glob("*.ttf"))
    assert (data_dir / "projects" / project_id / "font.json").is_file()


def test_ui_enqueue_job(api_client):
    client, _, _ = api_client
    r = client.post("/ui/projects", data={"name": "JobTest"}, follow_redirects=True)
    project_id = unquote(r.url.path.split("/projects/")[1].split("/ui")[0])
    r = client.post(
        f"/projects/{project_id}/ui/jobs",
        data={"kind": "full"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    live = client.get(f"/projects/{project_id}/ui/live")
    assert live.status_code == 200
    assert 'class="job-progress' in live.text
    assert 'role="progressbar"' in live.text
    assert "Общий прогресс" in live.text
    assert "0%" in live.text


def test_ui_delete_project(api_client):
    client, data_dir, _ = api_client
    r = client.post("/ui/projects", data={"name": "Rm"}, follow_redirects=True)
    project_id = unquote(r.url.path.split("/projects/")[1].split("/ui")[0])
    assert (data_dir / "projects" / project_id).is_dir()

    r = client.post(f"/projects/{project_id}/ui/delete", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/app"
    assert not (data_dir / "projects" / project_id).exists()

    r = client.get("/app")
    assert "Rm" not in r.text


def test_locale_from_accept_language(api_client):
    client, _, _ = api_client
    logout_session(client)
    client.cookies.clear()
    r = client.get("/", headers={"Accept-Language": "ja,en;q=0.8"})
    assert r.status_code == 200
    assert 'lang="ja"' in r.text
    assert "巻全体" in r.text or "一冊の巻" in r.text


def test_locale_cookie_overrides_accept_language(api_client):
    client, _, _ = api_client
    logout_session(client)
    r = client.post(
        "/locale",
        data={"lang": "en", "next": "/"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert "comic_locale" in r.cookies
    assert r.cookies["comic_locale"] == "en"
    r = client.get("/", headers={"Accept-Language": "ru"})
    assert r.status_code == 200
    assert 'lang="en"' in r.text
    assert "One issue" in r.text


def test_locale_unknown_falls_back_to_en(api_client):
    client, _, _ = api_client
    logout_session(client)
    client.cookies.clear()
    r = client.get("/", headers={"Accept-Language": "xx-XX,zz;q=0.5"})
    assert r.status_code == 200
    assert 'lang="en"' in r.text
    assert "One issue" in r.text


def test_landing_has_faq_and_privacy(api_client):
    client, _, _ = api_client
    logout_session(client)
    r = client.get("/")
    assert r.status_code == 200
    assert 'id="faq"' in r.text
    assert "privacy-block" in r.text or "privacy-panel" in r.text
    assert "compare-table" in r.text
    assert 'name="description"' in r.text


def test_live_shows_preview_and_translations_link(api_client, tmp_path):
    from PIL import Image
    import io

    client, data_dir, _ = api_client
    r = client.post("/ui/projects", data={"name": "Prev"}, follow_redirects=True)
    project_id = r.url.path.split("/projects/")[1].split("/ui")[0]

    out = ProjectPaths.for_project(data_dir, project_id).out_dir
    out.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
    (out / "01.png").write_bytes(buf.getvalue())
    ProjectPaths.for_project(data_dir, project_id).translations_path.write_text("{}")

    live = client.get(f"/projects/{project_id}/ui/live")
    assert live.status_code == 200
    assert "page-preview-grid" in live.text
    assert "/download/translations" in live.text


def test_locale_switcher_present_on_pages(api_client):
    client, _, _ = api_client
    for path in ("/", "/pricing", "/app", "/login"):
        if path in ("/app",):
            r = client.get(path)
        else:
            if path == "/login":
                logout_session(client)
            r = client.get(path)
        assert r.status_code == 200, path
        assert 'action="/locale"' in r.text
        assert 'name="lang"' in r.text
