"""Загрузка шрифта проекта."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from service.paths import ProjectPaths
from service.project_font import (
    MAX_FONT_BYTES,
    load_project_font,
    project_font_path_for_repo,
    save_project_font,
)
from service.pipeline_job_options import defaults_for_form
from service.runtime import RuntimeSettings


def _minimal_ttf_bytes() -> bytes:
    """Минимальный валидный TTF из Pillow (DejaVu или системный)."""
    from render import DEFAULT_FONT_CANDIDATES

    for path in DEFAULT_FONT_CANDIDATES:
        p = Path(path)
        if p.is_file():
            return p.read_bytes()
    pytest.skip("Нет системного TTF для теста")


def test_save_project_font(tmp_path: Path):
    data_dir = tmp_path / "data"
    paths = ProjectPaths.for_project(data_dir, "Issue 1")
    result = save_project_font(paths, filename="My Font.ttf", data=_minimal_ttf_bytes())
    assert result.ok
    assert result.info is not None
    assert (paths.fonts_dir / result.info.job_font_path.split("/")[-1]).is_file()
    assert paths.font_meta_path.is_file()
    loaded = load_project_font(paths)
    assert loaded is not None
    assert loaded.display_name == "My Font.ttf"


def test_save_project_font_rejects_non_font(tmp_path: Path):
    paths = ProjectPaths.for_project(tmp_path / "data", "x")
    result = save_project_font(paths, filename="bad.ttf", data=b"not-a-font")
    assert not result.ok


def test_save_project_font_rejects_wrong_extension(tmp_path: Path):
    paths = ProjectPaths.for_project(tmp_path / "data", "x")
    result = save_project_font(paths, filename="x.woff2", data=_minimal_ttf_bytes())
    assert not result.ok


def test_save_project_font_rejects_oversized(tmp_path: Path):
    paths = ProjectPaths.for_project(tmp_path / "data", "x")
    result = save_project_font(
        paths,
        filename="huge.ttf",
        data=b"\x00" * (MAX_FONT_BYTES + 1),
    )
    assert not result.ok


def test_project_font_path_for_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    data_dir = repo / "data"
    paths = ProjectPaths.for_project(data_dir, "comic")
    save_project_font(paths, filename="a.ttf", data=_minimal_ttf_bytes())
    rel = project_font_path_for_repo(repo, paths)
    assert rel
    assert rel.startswith("data/projects/comic/fonts/")
    assert (repo / rel).is_file()


def test_defaults_for_form_uses_project_font(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    data_dir = repo / "data"
    paths = ProjectPaths.for_project(data_dir, "c")
    save_project_font(paths, filename="custom.otf", data=_minimal_ttf_bytes())
    settings = RuntimeSettings(
        repo_root=repo,
        config_path=repo / "config.toml",
        data_dir=data_dir,
        database_path=data_dir / "app.db",
        mit_repo=None,
        api_key=None,
        model="m",
        pages_per_batch=30,
        target_lang="ru",
        font_path="fonts/IrinaCTT.ttf",
        font_scale=1.2,
        host="127.0.0.1",
        port=8000,
        worker_poll_sec=2.0,
        ui_developer_mode=False,
        session_secret="test",
        session_https_only=True,
        allow_register=True,
        app_env="release",
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
    defaults = defaults_for_form(settings, repo_root=repo, paths=paths)
    assert defaults["font_path"].startswith("data/projects/c/fonts/")
    assert defaults["font_path"] != "fonts/IrinaCTT.ttf"
