"""COMIC_APP_ENV=dev: без cookie, developer UI, local user."""

from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from render import DEFAULT_FONT_CANDIDATES
from service import db as dbmod
from service.runtime import resolve_runtime
from service.users import LOCAL_DEV_EMAIL, get_user_by_email
from tests.conftest import _TEST_SESSION_SECRET


@pytest.fixture
def dev_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = Path(__file__).resolve().parent.parent
    data_dir = tmp_path / "data"
    db_path = data_dir / "app.db"
    font = next(
        (p for p in DEFAULT_FONT_CANDIDATES if Path(p).is_file()),
        None,
    )
    if font is None:
        pytest.skip("Нет системного шрифта")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[render]\nfont_path = "{font}"\nfont_scale = 1.0\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("COMIC_SESSION_SECRET", _TEST_SESSION_SECRET)
    monkeypatch.setenv("COMIC_APP_ENV", "dev")
    monkeypatch.setenv("COMIC_SESSION_HTTPS_ONLY", "0")
    monkeypatch.setenv("COMIC_PUBLIC_BASE_URL", "http://testserver")

    def _settings():
        base = resolve_runtime(repo_root=repo, config_path=cfg)
        return replace(
            base,
            data_dir=data_dir,
            database_path=db_path,
            session_secret=_TEST_SESSION_SECRET,
            app_env="dev",
            ui_developer_mode=True,
            public_base_url="http://testserver",
        )

    monkeypatch.setattr("api.resolve_runtime", _settings)
    monkeypatch.setattr("service.auth_deps.resolve_runtime", _settings)
    from api import app

    with TestClient(app) as client:
        client.headers["Accept-Language"] = "ru"
        client.cookies.clear()
        yield client, data_dir, _settings


def test_dev_app_without_login(dev_client):
    client, _, get_settings = dev_client
    assert get_settings().is_dev
    assert get_settings().ui_developer_mode is True

    r = client.get("/app")
    assert r.status_code == 200
    assert "Мои проекты" in r.text

    conn = dbmod.connect(get_settings().database_path)
    try:
        user = get_user_by_email(conn, LOCAL_DEV_EMAIL)
        assert user is not None
    finally:
        conn.close()


def test_dev_create_project_as_local_user(dev_client):
    client, _, get_settings = dev_client
    r = client.post("/ui/projects", data={"name": "DevProj"}, follow_redirects=False)
    assert r.status_code == 303
    pid = unquote(r.headers["location"].split("/projects/")[1].split("/ui")[0])

    conn = dbmod.connect(get_settings().database_path)
    try:
        user = get_user_by_email(conn, LOCAL_DEV_EMAIL)
        project = dbmod.get_project(conn, pid)
        assert user is not None
        assert project is not None
        assert project.user_id == user.id
    finally:
        conn.close()


def test_dev_login_redirects_to_app(dev_client):
    client, _, _ = dev_client
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/app"
