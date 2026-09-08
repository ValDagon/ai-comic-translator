"""Выполнение job: сборка PipelineConfig и run_pipeline."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pipeline import PipelineConfig, PipelineError, PipelineMode, run_pipeline
from service.db import JobRow, append_job_log, finish_job, get_project, touch_job_heartbeat
from service.paths import ProjectPaths
from service.pipeline_job_options import (
    JobOptions,
    font_path_allowed,
    merge_with_runtime,
    sanitize_client_job_options,
    validate_job_options,
)
from service.project_font import project_font_path_for_repo
from service.runtime import RuntimeSettings
from service.user_log import line_for_job_db

KIND_TO_MODE = {
    "extract": PipelineMode.EXTRACT,
    "translate": PipelineMode.TRANSLATE,
    "render": PipelineMode.RENDER,
    "full": PipelineMode.FULL,
}


def _resolve_mit_repo(settings: RuntimeSettings, mit_override: str | None) -> Path | None:
    # Release jobs never honor client mit_repo (stripped earlier); keep for stored
    # legacy options_json from older clients / CLI-equivalent worker paths.
    if settings.is_dev and mit_override:
        path = Path(mit_override).expanduser()
        if not path.is_absolute():
            path = settings.repo_root / path
        return path.resolve() if path.is_dir() else None
    return settings.mit_repo


def run_job(conn: sqlite3.Connection, job: JobRow, settings: RuntimeSettings) -> None:
    mode = KIND_TO_MODE.get(job.kind)
    if mode is None:
        finish_job(conn, job.id, ok=False, error=f"Неизвестный kind: {job.kind}")
        return

    opts = sanitize_client_job_options(JobOptions.from_json(job.options_json), settings)
    validation_err = validate_job_options(job.kind, opts)
    if validation_err:
        finish_job(conn, job.id, ok=False, error=validation_err)
        return

    project = get_project(conn, job.project_id)
    if project is None:
        finish_job(conn, job.id, ok=False, error="Проект не найден")
        return

    paths = ProjectPaths.for_project(settings.data_dir, job.project_id)
    paths.ensure_dirs()

    resolved = merge_with_runtime(settings, opts)
    project_font = project_font_path_for_repo(settings.repo_root, paths)
    if project_font:
        resolved.font_path = project_font
    if resolved.font_path and not font_path_allowed(
        resolved.font_path,
        repo_root=settings.repo_root,
        data_dir=settings.data_dir,
        project_id=job.project_id,
    ):
        finish_job(
            conn,
            job.id,
            ok=False,
            error="font_path вне разрешённых каталогов",
        )
        return

    mit_repo = _resolve_mit_repo(settings, resolved.mit_repo)

    if job.kind in ("translate", "render"):
        skip_extract = True
    elif job.kind == "full":
        skip_extract = resolved.skip_extract
    else:
        skip_extract = False

    if job.kind in ("extract", "full") and not skip_extract and mit_repo is None:
        finish_job(
            conn,
            job.id,
            ok=False,
            error="Укажите mit_repo в config.toml [server] или MIT_REPO",
        )
        return

    if job.kind == "render":
        translations_in = paths.translations_path
    elif job.kind in ("translate", "full") and resolved.use_existing_translations:
        translations_in = paths.translations_path
    else:
        translations_in = None

    if job.kind in ("translate", "full") and resolved.save_translations:
        translations_out = paths.translations_path
    else:
        translations_out = None

    def log_fn(msg: str) -> None:
        print(f"[job {job.id[:8]}] {msg}", flush=True)
        short = line_for_job_db(msg)
        if short:
            append_job_log(conn, job.id, short)
        # Any progress line = job isn't stuck; keeps the stale-job watchdog
        # (service.db.recover_stale_running_jobs) from failing a slow-but-alive job.
        touch_job_heartbeat(conn, job.id)

    log_fn(f"Старт job ({job.kind})…")

    cfg = PipelineConfig(
        input_dir=paths.input_dir,
        out_dir=paths.out_dir,
        clean_dir=paths.clean_dir,
        mode=mode,
        mit_repo=mit_repo,
        skip_extract=skip_extract,
        prep_manual=resolved.prep_manual,
        strict=resolved.strict,
        target_lang=resolved.target_lang,
        translations_out=translations_out,
        translations_in=translations_in,
        font_path=resolved.font_path,
        font_scale=resolved.font_scale,
        model=resolved.model,
        pages_per_batch=resolved.pages_per_batch,
        api_key=settings.api_key,
        project_root=settings.repo_root,
    )

    try:
        run_pipeline(cfg, log=log_fn)
    except PipelineError as e:
        log_fn(str(e))
        finish_job(conn, job.id, ok=False, error=str(e))
        return
    except Exception as e:
        log_fn(str(e))
        finish_job(conn, job.id, ok=False, error=str(e))
        return

    if finish_job(conn, job.id, ok=True):
        from service.job_notify import notify_job_ready

        try:
            notify_job_ready(conn, job, settings)
        except Exception as exc:  # noqa: BLE001 — mail must not fail the job
            print(f"[job {job.id[:8]}] job-ready email failed: {exc}", flush=True)


def process_one_job(conn: sqlite3.Connection, settings: RuntimeSettings) -> bool:
    from service.db import claim_next_job

    job = claim_next_job(conn)
    if not job:
        return False
    try:
        run_job(conn, job, settings)
    except Exception as e:
        print(f"[job {job.id[:8]}] ERROR: {e}", flush=True)
        append_job_log(conn, job.id, str(e))
        finish_job(conn, job.id, ok=False, error=str(e))
    return True
