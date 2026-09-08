"""job_runner: fail paths без реального MIT/OpenRouter."""

from __future__ import annotations

from pathlib import Path

from service import db as dbmod
from service.job_runner import process_one_job, run_job
from service.paths import ProjectPaths
from service.runtime import RuntimeSettings


def _runtime(tmp_path: Path, *, mit_repo: Path | None = None) -> RuntimeSettings:
    data_dir = tmp_path / "data"
    return RuntimeSettings(
        repo_root=tmp_path,
        config_path=tmp_path / "config.toml",
        data_dir=data_dir,
        database_path=data_dir / "app.db",
        mit_repo=mit_repo,
        api_key=None,
        model="test/model",
        pages_per_batch=30,
        target_lang="ru",
        font_path=None,
        font_scale=1.0,
        host="127.0.0.1",
        port=8000,
        worker_poll_sec=0.1,
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


def test_job_runner_fails_without_mit_repo_for_extract(tmp_path: Path):
    settings = _runtime(tmp_path, mit_repo=None)
    conn = dbmod.connect(settings.database_path)
    dbmod.init_db(conn)
    project = dbmod.create_project(conn, "no-mit", data_dir=settings.data_dir)
    ProjectPaths.for_project(settings.data_dir, project.id).ensure_dirs()
    job = dbmod.create_job(conn, project.id, "extract")

    run_job(conn, job, settings)
    updated = dbmod.get_job(conn, job.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error and "mit_repo" in updated.error.lower()
    conn.close()


def test_job_runner_fails_on_pipeline_error(tmp_path: Path, minimal_dir: Path):
    settings = _runtime(tmp_path)
    conn = dbmod.connect(settings.database_path)
    dbmod.init_db(conn)
    project = dbmod.create_project(conn, "bad-render", data_dir=settings.data_dir)
    paths = ProjectPaths.for_project(settings.data_dir, project.id)
    paths.ensure_dirs()
    (paths.input_dir / "1_translations.txt").write_bytes(
        (minimal_dir / "1_translations.txt").read_bytes()
    )
    (paths.input_dir / "1.jpg").write_bytes(b"x")
    # нет translations.json → render должен упасть
    job = dbmod.create_job(conn, project.id, "render")

    assert process_one_job(conn, settings)
    updated = dbmod.get_job(conn, job.id)
    assert updated is not None
    assert updated.status == "failed"
    assert updated.error
    conn.close()


def test_process_one_job_empty_queue(tmp_path: Path):
    settings = _runtime(tmp_path)
    conn = dbmod.connect(settings.database_path)
    dbmod.init_db(conn)
    assert process_one_job(conn, settings) is False
    conn.close()
