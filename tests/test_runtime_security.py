"""Runtime fail-closed security checks."""

import pytest

from service.runtime import (
    INSECURE_DEV_SESSION_SECRET,
    RuntimeSecurityError,
    RuntimeSettings,
    assert_runtime_secure,
)
from pathlib import Path


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
        target_lang="ru",
        font_path=None,
        font_scale=1.2,
        host="127.0.0.1",
        port=8000,
        worker_poll_sec=2.0,
        ui_developer_mode=False,
        session_secret="s" * 32,
        session_https_only=True,
        allow_register=True,
        app_env="release",
        public_base_url="https://example.com",
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


def test_release_rejects_default_secret():
    with pytest.raises(RuntimeSecurityError, match="COMIC_SESSION_SECRET"):
        assert_runtime_secure(
            _settings(session_secret=INSECURE_DEV_SESSION_SECRET)
        )


def test_release_rejects_short_secret():
    with pytest.raises(RuntimeSecurityError, match="COMIC_SESSION_SECRET"):
        assert_runtime_secure(_settings(session_secret="short"))


def test_release_accepts_strong_secret():
    assert_runtime_secure(_settings(session_secret="x" * 32))


def test_release_rejects_non_https_only_cookie():
    with pytest.raises(RuntimeSecurityError, match="COMIC_SESSION_HTTPS_ONLY"):
        assert_runtime_secure(_settings(session_https_only=False))


def test_release_accepts_https_only_cookie():
    assert_runtime_secure(_settings(session_https_only=True))


def test_dev_on_public_host_requires_flag(monkeypatch):
    monkeypatch.delenv("COMIC_ALLOW_INSECURE_DEV", raising=False)
    with pytest.raises(RuntimeSecurityError, match="COMIC_ALLOW_INSECURE_DEV"):
        assert_runtime_secure(
            _settings(
                app_env="dev",
                ui_developer_mode=True,
                host="0.0.0.0",
                session_secret=INSECURE_DEV_SESSION_SECRET,
            )
        )


def test_dev_on_loopback_ok():
    assert_runtime_secure(
        _settings(
            app_env="dev",
            ui_developer_mode=True,
            host="127.0.0.1",
            session_secret=INSECURE_DEV_SESSION_SECRET,
        )
    )
