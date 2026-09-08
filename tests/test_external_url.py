"""Public base URL behind proxies."""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request

from service.external_url import external_base_url
from service.runtime import RuntimeSettings


def _settings(**overrides) -> RuntimeSettings:
    base = dict(
        repo_root=Path("."),
        config_path=Path("."),
        data_dir=Path("data"),
        database_path=Path("data/app.db"),
        mit_repo=None,
        api_key=None,
        model="x",
        pages_per_batch=30,
        target_lang="RU",
        font_path=None,
        font_scale=1.2,
        host="127.0.0.1",
        port=8080,
        worker_poll_sec=2.0,
        ui_developer_mode=False,
        session_secret="x" * 32,
        session_https_only=True,
        allow_register=True,
        app_env="release",
        public_base_url="http://127.0.0.1:8080",
        google_client_id="",
        google_client_secret="",
        apple_client_id="",
        apple_team_id="",
        apple_key_id="",
        apple_private_key="",
        smtp_host="",
        smtp_port=587,
        smtp_username="",
        smtp_password="",
        smtp_from="",
        smtp_use_tls=True,
    )
    base.update(overrides)
    return RuntimeSettings(**base)


def test_external_base_url_prefers_explicit_public_url(monkeypatch):
    monkeypatch.setenv("COMIC_PUBLIC_BASE_URL", "https://comics.example.com")
    settings = _settings(public_base_url="https://comics.example.com")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/login",
        "headers": [
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", b"evil.example"),
        ],
        "scheme": "http",
        "server": ("127.0.0.1", 8080),
        "client": ("127.0.0.1", 12345),
    }
    request = Request(scope)
    assert external_base_url(request, settings) == "https://comics.example.com"


def test_external_base_url_falls_back_to_forwarded_when_loopback_public(monkeypatch):
    monkeypatch.delenv("COMIC_PUBLIC_BASE_URL", raising=False)
    settings = _settings(public_base_url="http://127.0.0.1:8080")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/login",
        "headers": [
            (b"x-forwarded-proto", b"https"),
            (b"x-forwarded-host", b"my-service.run.app"),
        ],
        "scheme": "http",
        "server": ("127.0.0.1", 8080),
        "client": ("127.0.0.1", 12345),
    }
    request = Request(scope)
    assert external_base_url(request, settings) == "https://my-service.run.app"
