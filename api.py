"""FastAPI: проекты, загрузка страниц, очередь job'ов."""

from __future__ import annotations

import io
import os
import sqlite3
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from service import db as dbmod
from service.access_log import quiet_uvicorn_access_log
from service.auth_deps import (
    LoginRedirect,
    get_conn,
    get_settings,
    login_url,
    require_project_for_user,
    require_user,
)
from service.ingest_upload import ingest_upload_files
from service.paths import ProjectPaths
from service.pipeline_job_options import (
    JobOptions,
    sanitize_client_job_options,
    validate_job_options,
)
from service.project_cleanup import ProjectBusyError, list_projects_on_disk
from service.quotas import enqueue_block_reason
from service.runtime import (
    RuntimeSecurityError,
    RuntimeSettings,
    assert_runtime_secure,
    resolve_runtime,
)
from service.users import UserRow
from web.account_routes import router as account_router
from web.auth_routes import router as auth_router
from web.i18n import LocaleMiddleware, get_locale, router as locale_router, t
from web.routes import ROOT as WEB_ROOT
from web.routes import _project_ui_path, router as web_router


class CreateProjectBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CreateJobBody(BaseModel):
    kind: Literal["extract", "translate", "render", "full"]
    options: dict | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    created_at: str


class JobOut(BaseModel):
    id: str
    project_id: str
    kind: str
    status: str
    log: str
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None


@asynccontextmanager
async def lifespan(app: FastAPI):
    quiet_uvicorn_access_log()
    settings = resolve_runtime()
    try:
        assert_runtime_secure(settings)
    except RuntimeSecurityError as exc:
        raise SystemExit(f"[startup] {exc}") from exc
    conn = dbmod.connect(settings.database_path)
    dbmod.init_db(conn)
    # Recover running jobs only in worker — API restart must not fail live work.
    removed = dbmod.purge_projects_without_folder(conn, settings.data_dir)
    if removed:
        print(f"[startup] убраны проекты без папки на диске: {', '.join(removed)}", flush=True)
    conn.close()
    app.state.settings = settings
    yield


app = FastAPI(title="Comic Translator API", lifespan=lifespan)
_boot_settings = resolve_runtime()
app.add_middleware(
    SessionMiddleware,
    secret_key=_boot_settings.session_secret,
    session_cookie="comic_session",
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=_boot_settings.session_https_only,
)
app.add_middleware(LocaleMiddleware)
# Only trust X-Forwarded-* from private-network peers by default (the Caddy/
# docker-compose reverse proxy in front of this API). A "*" default would let
# ANY client spoof X-Forwarded-Proto/Host if the API port is ever exposed
# directly (misconfigured network, no proxy in front, etc). Cloud Run's own
# edge proxy is not on a private IP, so that deployment must opt back into
# COMIC_PROXY_TRUSTED_HOSTS=* explicitly (see docs/deploy-gcp.md). OAuth URLs
# prefer COMIC_PUBLIC_BASE_URL regardless (see service.external_url).
_DEFAULT_PROXY_TRUSTED_HOSTS = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
_proxy_trusted = (
    os.environ.get("COMIC_PROXY_TRUSTED_HOSTS") or _DEFAULT_PROXY_TRUSTED_HOSTS
).strip()
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=_proxy_trusted)
app.include_router(locale_router)
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(web_router)
app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _safe_out_filename(name: str) -> str | None:
    """Basename only; must be an image in out/."""
    base = Path(name).name
    if not base or base != name.strip() or ".." in name:
        return None
    if Path(base).suffix.lower() not in _IMAGE_SUFFIXES:
        return None
    return base


@app.exception_handler(LoginRedirect)
async def login_redirect_handler(_request: Request, exc: LoginRedirect):
    return RedirectResponse(url=login_url(exc.next_path), status_code=303)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if "/ui/upload" in request.url.path and "/projects/" in request.url.path:
        pid = unquote(request.url.path.split("/projects/", 1)[1].split("/")[0])
        print(f"[upload] 422 validation project={pid}: {exc.errors()}", flush=True)
        locale = get_locale(request)
        return RedirectResponse(
            url=_project_ui_path(
                pid,
                query={
                    "upload_error": t("err.upload_rejected", locale=locale),
                },
            ),
            status_code=303,
        )
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/projects", response_model=ProjectOut)
def create_project(
    body: CreateProjectBody,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
):
    row = dbmod.create_project(
        conn,
        body.name.strip(),
        data_dir=settings.data_dir,
        user_id=user.id,
    )
    paths = ProjectPaths.for_project(settings.data_dir, row.id)
    paths.ensure_dirs()
    return ProjectOut(id=row.id, name=row.name, created_at=row.created_at)


@app.get("/projects", response_model=list[ProjectOut])
def list_projects(
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
):
    return [
        ProjectOut(id=p.id, name=p.name, created_at=p.created_at)
        for p in list_projects_on_disk(conn, settings.data_dir, user_id=user.id)
    ]


@app.delete("/projects/{project_id}", status_code=204)
def delete_project_api(
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
):
    from service.project_cleanup import delete_project_fully

    require_project_for_user(conn, project_id, user)
    try:
        delete_project_fully(conn, settings.data_dir, project_id)
    except ProjectBusyError:
        raise HTTPException(409, "project has an active job") from None


@app.get("/projects/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[UserRow, Depends(require_user)],
):
    row = require_project_for_user(conn, project_id, user)
    return ProjectOut(id=row.id, name=row.name, created_at=row.created_at)


@app.post("/projects/{project_id}/pages")
def upload_pages(
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
    files: list[UploadFile] = File(...),
):
    require_project_for_user(conn, project_id, user)
    if not files:
        raise HTTPException(400, "no files")
    paths = ProjectPaths.for_project(settings.data_dir, project_id)
    result = ingest_upload_files(paths.input_dir, files)
    if not result.saved:
        raise HTTPException(400, "no valid filenames")
    return {
        "saved": result.saved,
        "skipped": [{"name": s.name, "reason": s.reason} for s in result.skipped],
    }


@app.post("/projects/{project_id}/jobs", response_model=JobOut)
def enqueue_job(
    request: Request,
    project_id: str,
    body: CreateJobBody,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
):
    require_project_for_user(conn, project_id, user)
    locale = get_locale(request)
    blocked = enqueue_block_reason(
        conn, project_id=project_id, user_id=user.id, locale=locale
    )
    if blocked:
        raise HTTPException(409, blocked)
    opts = sanitize_client_job_options(
        JobOptions.from_mapping(body.options or {}),
        settings,
    )
    err = validate_job_options(body.kind, opts, locale=locale)
    if err:
        raise HTTPException(400, err)
    job = dbmod.create_job(conn, project_id, body.kind, options_json=opts.to_json())
    return _job_out(job)


@app.get("/projects/{project_id}/jobs", response_model=list[JobOut])
def list_jobs(
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[UserRow, Depends(require_user)],
):
    require_project_for_user(conn, project_id, user)
    return [_job_out(j) for j in dbmod.list_jobs(conn, project_id)]


@app.get("/projects/{project_id}/jobs/{job_id}", response_model=JobOut)
def get_job(
    project_id: str,
    job_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[UserRow, Depends(require_user)],
):
    require_project_for_user(conn, project_id, user)
    job = dbmod.get_job(conn, job_id)
    if not job or job.project_id != project_id:
        raise HTTPException(404, "job not found")
    return _job_out(job)


def _content_disposition_attachment(filename: str) -> str:
    """RFC 5987: ASCII fallback + UTF-8 filename* (non-ASCII project ids)."""
    ascii_name = (
        filename.encode("ascii", "replace").decode("ascii").replace("?", "_")
        or "out.zip"
    )
    return (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )


@app.get("/projects/{project_id}/download")
def download_out_zip(
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
):
    require_project_for_user(conn, project_id, user)
    out_dir = ProjectPaths.for_project(settings.data_dir, project_id).out_dir
    if not out_dir.is_dir():
        raise HTTPException(404, "no output yet")
    files = [p for p in out_dir.iterdir() if p.is_file()]
    if not files:
        raise HTTPException(404, "output folder empty")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(files):
            zf.write(path, arcname=path.name)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": _content_disposition_attachment(
                f"{project_id}-out.zip"
            )
        },
    )


@app.get("/projects/{project_id}/download/translations")
def download_translations_json(
    project_id: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
):
    require_project_for_user(conn, project_id, user)
    path = ProjectPaths.for_project(settings.data_dir, project_id).translations_path
    if not path.is_file():
        raise HTTPException(404, "no translations yet")
    return FileResponse(
        path,
        media_type="application/json",
        filename=f"{project_id}-translations.json",
        headers={
            "Content-Disposition": _content_disposition_attachment(
                f"{project_id}-translations.json"
            )
        },
    )


@app.get("/projects/{project_id}/out/{filename}")
def serve_out_page(
    project_id: str,
    filename: str,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
):
    require_project_for_user(conn, project_id, user)
    safe = _safe_out_filename(filename)
    if not safe:
        raise HTTPException(404, "invalid filename")
    path = ProjectPaths.for_project(settings.data_dir, project_id).out_dir / safe
    if not path.is_file():
        raise HTTPException(404, "page not found")
    return FileResponse(path)


def _job_out(job: dbmod.JobRow) -> JobOut:
    return JobOut(
        id=job.id,
        project_id=job.project_id,
        kind=job.kind,
        status=job.status,
        log=job.log,
        error=job.error,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )
