"""Простой веб-UI (Jinja2 + HTMX) поверх того же SQLite/API."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from service import db as dbmod
from service.auth_deps import (
    get_conn,
    get_settings,
    optional_user,
    require_project_for_user,
    require_user,
)
from service.ingest_upload import ingest_upload_files
from service.paths import ProjectPaths
from service.pipeline_job_options import (
    defaults_for_form,
    job_options_from_form,
    sanitize_client_job_options,
    target_language_choices,
    validate_job_options,
)
from service.format_when import format_when
from service.project_cleanup import (
    ProjectBusyError,
    delete_project_fully,
    get_project_on_disk,
    list_projects_on_disk,
)
from service.project_font import load_project_font, save_project_font
from service.quotas import enqueue_block_reason
from service.runtime import RuntimeSettings
from service.upload_pages import skipped_for_user_warning
from service.users import UserRow
from web.i18n import I18nJinja2Templates, get_locale, t

ROOT = Path(__file__).resolve().parent.parent
templates = I18nJinja2Templates(directory=str(ROOT / "templates"))
templates.env.filters["urlquote"] = lambda v: quote(str(v), safe="")
templates.env.filters["format_when"] = format_when
templates.env.filters["tojson"] = lambda v: json.dumps(v, ensure_ascii=False)

router = APIRouter(tags=["ui"])

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _project_ui_path(project_id: str, *, query: dict[str, str] | None = None) -> str:
    base = f"/projects/{quote(project_id, safe='')}/ui"
    if not query:
        return base
    return f"{base}?{urlencode(query)}"


def _count_input_pages(input_dir: Path) -> int:
    if not input_dir.is_dir():
        return 0
    return sum(
        1 for p in input_dir.iterdir() if p.suffix.lower() in _IMAGE_SUFFIXES
    )


def _count_out_pages(out_dir: Path) -> int:
    return _count_input_pages(out_dir)


def _list_preview_pages(out_dir: Path, *, limit: int = 5) -> list[str]:
    if not out_dir.is_dir():
        return []
    names = sorted(
        p.name
        for p in out_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES
    )
    return names[:limit]


def _retention_days() -> int:
    try:
        days = int(os.environ.get("COMIC_PROJECT_RETENTION_DAYS", "3"))
    except ValueError:
        return 3
    return max(days, 1)


def _live_context(
    project_id: str,
    settings: RuntimeSettings,
    jobs: list[dbmod.JobRow],
) -> dict:
    from service.job_progress import project_progress

    paths = ProjectPaths.for_project(settings.data_dir, project_id)
    active = any(j.status in ("queued", "running") for j in jobs)
    input_count = _count_input_pages(paths.input_dir)
    out_count = _count_out_pages(paths.out_dir)
    progress = project_progress(
        jobs, input_count=input_count, out_count=out_count
    )
    return {
        "project_id": project_id,
        "jobs": jobs,
        "input_count": input_count,
        "out_count": out_count,
        "has_translations": paths.translations_path.is_file(),
        "preview_pages": _list_preview_pages(paths.out_dir),
        # relative to repo (parent of data_dir): e.g. data/projects/<id>
        "folder_path": str(paths.root.relative_to(settings.data_dir.parent)),
        "poll_fast": active,
        "progress": progress,
    }


def _project_card(
    conn: sqlite3.Connection,
    settings: RuntimeSettings,
    project: dbmod.ProjectRow,
    *,
    locale: str,
) -> dict:
    paths = ProjectPaths.for_project(settings.data_dir, project.id)
    jobs = dbmod.list_jobs(conn, project.id)
    input_count = _count_input_pages(paths.input_dir)
    out_count = _count_out_pages(paths.out_dir)
    active = any(j.status in ("queued", "running") for j in jobs)
    failed_recent = (
        not active
        and any(j.status == "failed" for j in jobs)
        and out_count == 0
    )
    if active:
        status_key, status_text = "translating", t("status.translating", locale=locale)
    elif failed_recent:
        status_key, status_text = "failed", t("status.failed", locale=locale)
    elif out_count > 0:
        status_key, status_text = "complete", t("status.complete", locale=locale)
    elif input_count > 0:
        status_key, status_text = "ready", t("status.ready", locale=locale)
    else:
        status_key, status_text = "empty", t("status.empty", locale=locale)
    return {
        "project": project,
        "input_count": input_count,
        "out_count": out_count,
        "status_key": status_key,
        "status_label": status_text,
    }


@router.get("/", response_class=HTMLResponse)
def ui_landing(
    request: Request,
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow | None, Depends(optional_user)],
):
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "user": user,
            "allow_register": settings.allow_register,
            "register_error": "",
            "google_enabled": bool(
                settings.google_client_id and settings.google_client_secret
            ),
            "apple_enabled": bool(
                settings.apple_client_id
                and settings.apple_team_id
                and settings.apple_key_id
                and settings.apple_private_key
            ),
            "retention_days": _retention_days(),
            "public_base_url": settings.public_base_url.rstrip("/"),
        },
    )


@router.get("/pricing", response_class=HTMLResponse)
def ui_pricing(
    request: Request,
    user: Annotated[UserRow | None, Depends(optional_user)],
):
    return templates.TemplateResponse(
        request,
        "pricing.html",
        {"user": user},
    )


@router.get("/app", response_class=HTMLResponse)
def ui_home(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
):
    locale = get_locale(request)
    projects = list_projects_on_disk(conn, settings.data_dir, user_id=user.id)
    project_cards = [
        _project_card(conn, settings, p, locale=locale) for p in projects
    ]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "projects": projects,
            "project_cards": project_cards,
            "user": user,
            "ui_developer_mode": settings.ui_developer_mode,
        },
    )


@router.post("/ui/projects")
def ui_create_project(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
    name: str = Form(""),
):
    label = name.strip() or t("project.default_name", locale=get_locale(request))
    row = dbmod.create_project(
        conn, label, data_dir=settings.data_dir, user_id=user.id
    )
    ProjectPaths.for_project(settings.data_dir, row.id).ensure_dirs()
    return RedirectResponse(url=_project_ui_path(row.id), status_code=303)


@router.get("/projects/{project_id}/ui", response_class=HTMLResponse)
def ui_project(
    request: Request,
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
):
    locale = get_locale(request)
    row = get_project_on_disk(
        conn, settings.data_dir, project_id, user_id=user.id
    )
    if not row:
        raise HTTPException(404, t("err.project_not_found", locale=locale))
    jobs = dbmod.list_jobs(conn, project_id)
    ctx = _live_context(project_id, settings, jobs)
    paths = ProjectPaths.for_project(settings.data_dir, project_id)
    project_font = load_project_font(paths)
    upload_ok = request.query_params.get("upload_ok")
    upload_skipped = request.query_params.get("upload_skipped")
    upload_many = request.query_params.get("upload_many")
    upload_error = request.query_params.get("upload_error", "")
    font_ok = request.query_params.get("font_ok")
    font_error = request.query_params.get("font_error", "")
    job_error = request.query_params.get("job_error", "")
    name_error = request.query_params.get("name_error", "")
    return templates.TemplateResponse(
        request,
        "project.html",
        {
            "project": row,
            "user": user,
            "jobs": jobs,
            "upload_ok": upload_ok,
            "upload_skipped": upload_skipped,
            "upload_many": upload_many,
            "upload_error": upload_error,
            "font_ok": font_ok,
            "font_error": font_error,
            "job_error": job_error,
            "name_error": name_error,
            "project_font": project_font,
            "job_defaults": defaults_for_form(
                settings,
                repo_root=settings.repo_root,
                paths=paths,
            ),
            "target_languages": target_language_choices(),
            "ui_developer_mode": settings.ui_developer_mode,
            **ctx,
        },
    )


@router.post("/projects/{project_id}/ui/name")
def ui_rename_project(
    request: Request,
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
    name: str = Form(...),
):
    locale = get_locale(request)
    require_project_for_user(conn, project_id, user)
    if not get_project_on_disk(
        conn, settings.data_dir, project_id, user_id=user.id
    ):
        raise HTTPException(404, t("err.project_not_found", locale=locale))
    label = name.strip()
    if not label:
        return RedirectResponse(
            url=_project_ui_path(
                project_id,
                query={"name_error": t("err.name_required", locale=locale)},
            ),
            status_code=303,
        )
    updated = dbmod.update_project_name(conn, project_id, label)
    if not updated:
        raise HTTPException(404, t("err.project_not_found", locale=locale))
    return RedirectResponse(url=_project_ui_path(project_id), status_code=303)


@router.get("/projects/{project_id}/ui/live", response_class=HTMLResponse)
def ui_live_partial(
    request: Request,
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
):
    locale = get_locale(request)
    if not get_project_on_disk(
        conn, settings.data_dir, project_id, user_id=user.id
    ):
        raise HTTPException(404, t("err.project_not_found", locale=locale))
    jobs = dbmod.list_jobs(conn, project_id)
    return templates.TemplateResponse(
        request,
        "partials/live.html",
        _live_context(project_id, settings, jobs),
    )


@router.post("/projects/{project_id}/ui/upload")
def ui_upload_pages(
    request: Request,
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
    files: Annotated[list[UploadFile], File()],
):
    locale = get_locale(request)
    require_project_for_user(conn, project_id, user)
    if not get_project_on_disk(
        conn, settings.data_dir, project_id, user_id=user.id
    ):
        raise HTTPException(404, t("err.project_not_found", locale=locale))

    def _err(msg: str) -> RedirectResponse:
        print(f"[upload] project={project_id} ERROR: {msg}", flush=True)
        return RedirectResponse(
            url=_project_ui_path(project_id, query={"upload_error": msg[:500]}),
            status_code=303,
        )

    if not files:
        return _err(t("err.no_files", locale=locale))

    try:
        paths = ProjectPaths.for_project(settings.data_dir, project_id)
        result = ingest_upload_files(paths.input_dir, files)
        print(
            f"[upload] project={project_id} files={len(files)} "
            f"saved={len(result.saved)} skipped={len(result.skipped)}",
            flush=True,
        )
        for s in result.skipped:
            print(f"[upload]   skip {s.name}: {s.reason}", flush=True)

        if not result.saved:
            return _err(t("err.no_images", locale=locale))

        query: dict[str, str] = {"upload_ok": str(len(result.saved))}
        if skipped_for_user_warning(result.skipped):
            query["upload_skipped"] = "1"
        if len(files) >= 50:
            query["upload_many"] = "1"
        return RedirectResponse(
            url=_project_ui_path(project_id, query=query),
            status_code=303,
        )
    except Exception as exc:
        import traceback

        traceback.print_exc()
        return _err(t("err.upload_failed", locale=locale, exc=exc))


@router.post("/projects/{project_id}/ui/font")
def ui_upload_font(
    request: Request,
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
    file: Annotated[UploadFile, File()],
):
    locale = get_locale(request)
    require_project_for_user(conn, project_id, user)
    if not get_project_on_disk(
        conn, settings.data_dir, project_id, user_id=user.id
    ):
        raise HTTPException(404, t("err.project_not_found", locale=locale))

    def _redirect(**query: str) -> RedirectResponse:
        return RedirectResponse(
            url=_project_ui_path(project_id, query=query),
            status_code=303,
        )

    raw_name = (file.filename or "").strip()
    if not raw_name:
        return _redirect(font_error=t("err.font_not_selected", locale=locale))

    try:
        data = file.file.read()
    except Exception as exc:
        return _redirect(
            font_error=t("err.font_read", locale=locale, exc=exc)
        )

    paths = ProjectPaths.for_project(settings.data_dir, project_id)
    result = save_project_font(paths, filename=raw_name, data=data, locale=locale)
    if not result.ok:
        return _redirect(
            font_error=(result.error or t("err.font_save", locale=locale))[:500]
        )

    return _redirect(font_ok=result.info.display_name if result.info else "1")


@router.post("/projects/{project_id}/ui/delete")
def ui_delete_project(
    request: Request,
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
):
    locale = get_locale(request)
    require_project_for_user(conn, project_id, user)
    try:
        delete_project_fully(conn, settings.data_dir, project_id)
    except ProjectBusyError:
        return RedirectResponse(
            url=_project_ui_path(
                project_id,
                query={"job_error": t("err.project_busy_delete", locale=locale)},
            ),
            status_code=303,
        )
    return RedirectResponse(url="/app", status_code=303)


@router.post("/projects/{project_id}/ui/jobs")
async def ui_enqueue_job(
    request: Request,
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
    kind: str = Form(...),
):
    locale = get_locale(request)
    require_project_for_user(conn, project_id, user)
    if not get_project_on_disk(
        conn, settings.data_dir, project_id, user_id=user.id
    ):
        raise HTTPException(404, t("err.project_not_found", locale=locale))
    if kind not in ("extract", "translate", "render", "full"):
        raise HTTPException(400, t("err.unknown_job", locale=locale))

    blocked = enqueue_block_reason(
        conn, project_id=project_id, user_id=user.id, locale=locale
    )
    if blocked:
        return RedirectResponse(
            url=_project_ui_path(project_id, query={"job_error": blocked[:500]}),
            status_code=303,
        )

    form = await request.form()
    opts = sanitize_client_job_options(job_options_from_form(dict(form)), settings)
    err = validate_job_options(kind, opts, locale=locale)
    if err:
        return RedirectResponse(
            url=_project_ui_path(project_id, query={"job_error": err[:500]}),
            status_code=303,
        )

    dbmod.create_job(conn, project_id, kind, options_json=opts.to_json())
    return RedirectResponse(url=_project_ui_path(project_id), status_code=303)
