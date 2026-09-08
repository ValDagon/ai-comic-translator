from pathlib import Path

import pytest

from service.pipeline_job_options import (
    JobOptions,
    sanitize_client_job_options,
    validate_job_options,
)
from service.runtime import RuntimeSettings
from translate_request import build_system_prompt, target_language


def _settings(*, is_dev: bool = False) -> RuntimeSettings:
    return RuntimeSettings(
        repo_root=Path("."),
        config_path=Path("."),
        data_dir=Path("data"),
        database_path=Path("data/app.db"),
        mit_repo=None,
        api_key=None,
        model="x-ai/grok-4.3",
        pages_per_batch=30,
        target_lang="ru",
        font_path=None,
        font_scale=1.2,
        host="127.0.0.1",
        port=8000,
        worker_poll_sec=2.0,
        ui_developer_mode=is_dev,
        session_secret="s" * 32,
        session_https_only=not is_dev,
        allow_register=True,
        app_env="dev" if is_dev else "release",
        public_base_url="http://127.0.0.1:8000",
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


def test_validate_skip_extract_on_extract_only():
    err = validate_job_options("extract", JobOptions(skip_extract=True))
    assert err is not None
    assert "extract" in err.lower()


def test_validate_prep_manual_with_skip_extract():
    err = validate_job_options(
        "full",
        JobOptions(skip_extract=True, prep_manual=True),
    )
    assert err is not None
    assert "prep-manual" in err


def test_validate_use_existing_and_no_save():
    err = validate_job_options(
        "translate",
        JobOptions(use_existing_translations=True, save_translations=False),
    )
    assert err is not None


def test_validate_ok_defaults():
    assert validate_job_options("full", JobOptions()) is None


def test_sanitize_strips_privileged_in_release():
    opts = JobOptions(
        mit_repo="/evil",
        font_path="/etc/passwd",
        model="openai/gpt-4.1-mini",
        target_lang="ru",
    )
    clean = sanitize_client_job_options(opts, _settings(is_dev=False))
    assert clean.mit_repo is None
    assert clean.font_path is None
    assert clean.model == "openai/gpt-4.1-mini"


def test_sanitize_keeps_overrides_in_dev():
    opts = JobOptions(mit_repo="/tmp/mit", font_path="fonts/x.ttf", model="x-ai/grok-4.3")
    clean = sanitize_client_job_options(opts, _settings(is_dev=True))
    assert clean.mit_repo == "/tmp/mit"
    assert clean.font_path == "fonts/x.ttf"


def test_target_language_unknown():
    with pytest.raises(ValueError, match="Неизвестный"):
        target_language("xx")


def test_build_system_prompt_ru():
    prompt = build_system_prompt(target_lang="ru")
    assert "русский" in prompt
    assert "английского" not in prompt
    assert "Исходный язык любой" in prompt


def test_build_system_prompt_de():
    prompt = build_system_prompt(target_lang="de")
    assert "немецкий" in prompt
    assert "английского" not in prompt
