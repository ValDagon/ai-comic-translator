"""Зависимости FastAPI: текущий пользователь и владение проектом."""

from __future__ import annotations

import sqlite3
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request

from service import db as dbmod
from service.runtime import RuntimeSettings, resolve_runtime
from service.users import UserRow, ensure_local_dev_user, get_user_by_id

SESSION_USER_KEY = "user_id"


class LoginRedirect(Exception):
    """HTML-запрос без сессии → редирект на /login (см. handler в api.py)."""

    def __init__(self, next_path: str = "/") -> None:
        self.next_path = next_path or "/"


def get_settings(request: Request) -> RuntimeSettings:
    cached = getattr(request.app.state, "settings", None)
    if isinstance(cached, RuntimeSettings):
        return cached
    return resolve_runtime()


def get_conn(settings: Annotated[RuntimeSettings, Depends(get_settings)]):
    conn = dbmod.connect(settings.database_path)
    try:
        yield conn
    finally:
        conn.close()


def _wants_html(request: Request) -> bool:
    path = request.url.path
    if (
        path == "/"
        or path == "/app"
        or path.startswith("/app/")
        or path.startswith("/ui/")
        or path.endswith("/ui")
        or "/ui/" in path
        or path in ("/login", "/register", "/logout")
        or path.startswith("/auth/")
        or path == "/account"
        or path.startswith("/account/")
    ):
        return True
    accept = request.headers.get("accept", "")
    return "text/html" in accept


def optional_user(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
) -> UserRow | None:
    uid = request.session.get(SESSION_USER_KEY)
    if uid and isinstance(uid, str):
        user = get_user_by_id(conn, uid)
        if user is not None:
            return user
    if settings.is_dev:
        user = ensure_local_dev_user(conn)
        request.session[SESSION_USER_KEY] = user.id
        return user
    return None


def require_user(
    request: Request,
    user: Annotated[UserRow | None, Depends(optional_user)],
) -> UserRow:
    if user is not None:
        return user
    if _wants_html(request):
        raise LoginRedirect(request.url.path)
    raise HTTPException(status_code=401, detail="authentication required")


def login_user(request: Request, user: UserRow) -> None:
    request.session.clear()
    request.session[SESSION_USER_KEY] = user.id


def logout_user(request: Request) -> None:
    request.session.clear()


def require_project_for_user(
    conn: sqlite3.Connection,
    project_id: str,
    user: UserRow,
) -> dbmod.ProjectRow:
    row = dbmod.get_project(conn, project_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="project not found")
    return row


def login_url(next_path: str = "/app") -> str:
    return f"/login?next={quote(next_path or '/app', safe='/')}"
