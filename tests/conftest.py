from dataclasses import replace
from pathlib import Path
import json
import os
from base64 import b64encode

import itsdangerous

_TEST_SESSION_SECRET = "test-session-secret-at-least-32-chars!!"

# Until api.SessionMiddleware is created on first import.
os.environ.setdefault("COMIC_SESSION_SECRET", _TEST_SESSION_SECRET)
os.environ.setdefault("COMIC_ALLOW_REGISTER", "1")
os.environ.setdefault("COMIC_SESSION_HTTPS_ONLY", "1")
os.environ.setdefault("COMIC_APP_ENV", "release")

import pytest
from fastapi.testclient import TestClient

from render import DEFAULT_FONT_CANDIDATES
from service import db as dbmod
from service.auth_deps import SESSION_USER_KEY
from service.runtime import resolve_runtime
from service.users import PROVIDER_GOOGLE, create_user, link_identity


def make_oauth_user(
    conn,
    *,
    email: str = "test@example.com",
    provider: str = PROVIDER_GOOGLE,
    subject: str | None = None,
):
    user = create_user(conn, email)
    link_identity(conn, user.id, provider, subject or f"{provider}-{user.id}")
    return user


def logout_session(client: TestClient) -> None:
    client.post("/logout", follow_redirects=False)
    # httpx TestClient не всегда снимает comic_session по Set-Cookie expires.
    client.cookies.clear()


def login_session(
    client: TestClient,
    user_id: str,
    *,
    secret: str = _TEST_SESSION_SECRET,
) -> None:
    """Подписать cookie comic_session (httpx TestClient без session_transaction)."""
    logout_session(client)
    payload = b64encode(
        json.dumps({SESSION_USER_KEY: user_id}).encode("utf-8")
    )
    signed = itsdangerous.TimestampSigner(str(secret)).sign(payload)
    client.cookies.set("comic_session", signed.decode("utf-8"))


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def minimal_dir(fixtures_dir: Path) -> Path:
    return fixtures_dir / "minimal"


@pytest.fixture
def font_path() -> str:
    for path in DEFAULT_FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    pytest.skip("Нет системного шрифта для тестов render")


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, font_path: str):
    repo = Path(__file__).resolve().parent.parent
    data_dir = tmp_path / "data"
    db_path = data_dir / "app.db"
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        f'[render]\nfont_path = "{font_path}"\nfont_scale = 1.0\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("COMIC_SESSION_SECRET", _TEST_SESSION_SECRET)
    monkeypatch.setenv("COMIC_ALLOW_REGISTER", "1")
    monkeypatch.setenv("COMIC_SESSION_HTTPS_ONLY", "1")
    monkeypatch.setenv("COMIC_PUBLIC_BASE_URL", "http://testserver")
    monkeypatch.setenv("COMIC_APP_ENV", "release")

    def _settings():
        base = resolve_runtime(repo_root=repo, config_path=cfg)
        return replace(
            base,
            data_dir=data_dir,
            database_path=db_path,
            session_secret=_TEST_SESSION_SECRET,
            allow_register=True,
            app_env="release",
            ui_developer_mode=False,
            public_base_url="http://testserver",
        )

    monkeypatch.setattr("api.resolve_runtime", _settings)
    monkeypatch.setattr("service.auth_deps.resolve_runtime", _settings)
    from api import app

    with TestClient(app) as client:
        # Keep existing RU UI assertions stable; device default is Accept-Language.
        client.headers["Accept-Language"] = "ru"
        conn = dbmod.connect(db_path)
        try:
            dbmod.init_db(conn)
            user = make_oauth_user(conn, email="test@example.com")
        finally:
            conn.close()
        login_session(client, user.id)
        yield client, data_dir, _settings
