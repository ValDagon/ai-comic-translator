"""Login / OAuth / logout (cookie session)."""

from __future__ import annotations

import logging
import secrets
import sqlite3
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from service.auth_deps import (
    get_conn,
    get_settings,
    login_user,
    logout_user,
    optional_user,
)
from service.external_url import external_base_url
from service.oauth import (
    OAuthConfigError,
    OAuthSettings,
    apple_authorize_url,
    apple_fetch_profile,
    google_authorize_url,
    google_fetch_profile,
)
from service.runtime import RuntimeSettings
from service.users import (
    PROVIDER_APPLE,
    PROVIDER_GOOGLE,
    UserRow,
    upsert_oauth_user,
)
from web.i18n import get_locale, t
from web.routes import templates

router = APIRouter(tags=["auth"])
log = logging.getLogger("comic.auth")

_OAUTH_STATE_KEY = "oauth_state"
_OAUTH_NEXT_KEY = "oauth_next"


def _safe_next(raw: str | None) -> str:
    if not raw:
        return "/app"
    path = unquote(raw)
    if not path.startswith("/") or path.startswith("//"):
        return "/app"
    if path == "/":
        return "/app"
    return path


def _dev_bypass() -> RedirectResponse:
    return RedirectResponse(url="/app", status_code=303)


def _oauth_settings(request: Request, settings: RuntimeSettings) -> OAuthSettings:
    base = external_base_url(request, settings)
    return OAuthSettings(
        public_base_url=base,
        google_client_id=settings.google_client_id,
        google_client_secret=settings.google_client_secret,
        apple_client_id=settings.apple_client_id,
        apple_team_id=settings.apple_team_id,
        apple_key_id=settings.apple_key_id,
        apple_private_key=settings.apple_private_key,
    )


def _oauth_settings_for_login(settings: RuntimeSettings) -> OAuthSettings:
    """Login page only checks whether OAuth credentials exist."""
    return OAuthSettings(
        public_base_url=settings.public_base_url,
        google_client_id=settings.google_client_id,
        google_client_secret=settings.google_client_secret,
        apple_client_id=settings.apple_client_id,
        apple_team_id=settings.apple_team_id,
        apple_key_id=settings.apple_key_id,
        apple_private_key=settings.apple_private_key,
    )


def _auth_page_context(
    request: Request,
    *,
    settings: RuntimeSettings,
    next_path: str = "/app",
    error: str = "",
):
    oauth = _oauth_settings_for_login(settings)
    return {
        "error": error,
        "next": _safe_next(next_path),
        "user": None,
        "allow_register": settings.allow_register,
        "google_enabled": oauth.google_enabled(),
        "apple_enabled": oauth.apple_enabled(),
        "register_error": error,
    }


def _begin_oauth(request: Request, next_path: str) -> str:
    state = secrets.token_urlsafe(24)
    request.session[_OAUTH_STATE_KEY] = state
    request.session[_OAUTH_NEXT_KEY] = _safe_next(next_path)
    return state


def _pop_oauth_state(request: Request, state: str | None) -> str | None:
    expected = request.session.pop(_OAUTH_STATE_KEY, None)
    next_path = request.session.pop(_OAUTH_NEXT_KEY, "/app")
    if not state or not expected or state != expected:
        return None
    return _safe_next(next_path if isinstance(next_path, str) else "/app")


def _login_error_redirect(error_key: str, next_path: str = "/app") -> RedirectResponse:
    from urllib.parse import quote

    return RedirectResponse(
        url=f"/login?next={quote(_safe_next(next_path), safe='/')}&error={quote(error_key)}",
        status_code=303,
    )


def _complete_oauth(
    request: Request,
    conn: sqlite3.Connection,
    settings: RuntimeSettings,
    *,
    provider: str,
    subject: str,
    email: str,
    dest: str,
) -> RedirectResponse:
    try:
        user = upsert_oauth_user(
            conn,
            provider=provider,
            provider_subject=subject,
            email=email,
            allow_register=settings.allow_register,
        )
    except ValueError as exc:
        return _login_error_redirect(str(exc), dest)
    login_user(request, user)
    return RedirectResponse(url=dest, status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow | None, Depends(optional_user)],
    next: str = "/app",
    error: str = "",
):
    if settings.is_dev:
        return _dev_bypass()
    if user:
        return RedirectResponse(url=_safe_next(next), status_code=303)
    locale = get_locale(request)
    err_text = t(error, locale=locale) if error.strip() else ""
    return templates.TemplateResponse(
        request,
        "login.html",
        _auth_page_context(
            request, settings=settings, next_path=next, error=err_text
        ),
    )


@router.get("/register", response_class=HTMLResponse)
def register_page(
    request: Request,
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow | None, Depends(optional_user)],
    next: str = "/app",
):
    if settings.is_dev:
        return _dev_bypass()
    if user:
        return RedirectResponse(url=_safe_next(next), status_code=303)
    return RedirectResponse(url="/#register", status_code=303)


@router.get("/auth/google")
def auth_google_start(
    request: Request,
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    next: str = "/app",
):
    if settings.is_dev:
        return _dev_bypass()
    oauth = _oauth_settings(request, settings)
    try:
        state = _begin_oauth(request, next)
        url = google_authorize_url(oauth, state=state)
        log.info("google oauth start redirect_uri=%s", oauth.callback_url(PROVIDER_GOOGLE))
    except OAuthConfigError:
        return _login_error_redirect("err.oauth_google_unconfigured", next)
    return RedirectResponse(url=url, status_code=303)


@router.get("/auth/google/callback")
def auth_google_callback(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if settings.is_dev:
        return _dev_bypass()
    dest = _pop_oauth_state(request, state)
    if dest is None:
        return _login_error_redirect("err.oauth_state")
    if error or not code:
        return _login_error_redirect("err.oauth_denied", dest)
    oauth = _oauth_settings(request, settings)
    try:
        profile = google_fetch_profile(oauth, code=code)
    except OAuthConfigError:
        return _login_error_redirect("err.oauth_google_unconfigured", dest)
    except ValueError as exc:
        key = str(exc)
        if key.startswith("err."):
            return _login_error_redirect(key, dest)
        log.exception(
            "google oauth callback failed redirect_uri=%s",
            oauth.callback_url(PROVIDER_GOOGLE),
        )
        return _login_error_redirect("err.oauth_failed", dest)
    except Exception:
        log.exception(
            "google oauth callback failed redirect_uri=%s",
            oauth.callback_url(PROVIDER_GOOGLE),
        )
        return _login_error_redirect("err.oauth_failed", dest)
    return _complete_oauth(
        request,
        conn,
        settings,
        provider=PROVIDER_GOOGLE,
        subject=profile.subject,
        email=profile.email,
        dest=dest,
    )


@router.get("/auth/apple")
def auth_apple_start(
    request: Request,
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    next: str = "/app",
):
    if settings.is_dev:
        return _dev_bypass()
    oauth = _oauth_settings(request, settings)
    try:
        state = _begin_oauth(request, next)
        url = apple_authorize_url(oauth, state=state)
    except OAuthConfigError:
        return _login_error_redirect("err.oauth_apple_unconfigured", next)
    return RedirectResponse(url=url, status_code=303)


@router.post("/auth/apple/callback")
def auth_apple_callback(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    code: str = Form(""),
    state: str = Form(""),
    id_token: str = Form(""),
    error: str = Form(""),
):
    if settings.is_dev:
        return _dev_bypass()
    dest = _pop_oauth_state(request, state or None)
    if dest is None:
        return _login_error_redirect("err.oauth_state")
    if error or not code:
        return _login_error_redirect("err.oauth_denied", dest)
    oauth = _oauth_settings(request, settings)
    try:
        profile = apple_fetch_profile(
            oauth, code=code, id_token_hint=id_token or None
        )
    except OAuthConfigError:
        return _login_error_redirect("err.oauth_apple_unconfigured", dest)
    except ValueError as exc:
        key = str(exc)
        if key.startswith("err."):
            return _login_error_redirect(key, dest)
        log.exception("apple oauth callback failed")
        return _login_error_redirect("err.oauth_failed", dest)
    except Exception:
        log.exception("apple oauth callback failed")
        return _login_error_redirect("err.oauth_failed", dest)
    return _complete_oauth(
        request,
        conn,
        settings,
        provider=PROVIDER_APPLE,
        subject=profile.subject,
        email=profile.email,
        dest=dest,
    )


@router.post("/logout")
def logout_submit(
    request: Request,
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
):
    if settings.is_dev:
        return _dev_bypass()
    logout_user(request)
    return RedirectResponse(url="/", status_code=303)
