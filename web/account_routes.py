"""Личный кабинет: профиль, заглушки подписки и billing."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from service.auth_deps import get_conn, get_settings, logout_user, require_user
from service.mailer import smtp_from_runtime, send_email
from service.project_cleanup import ProjectBusyError, delete_project_fully, list_projects_on_disk
from service.runtime import RuntimeSettings
from service.users import (
    PURPOSE_DELETE_ACCOUNT,
    UserRow,
    consume_email_action_token,
    create_email_action_token,
    delete_user,
    get_user_by_id,
    list_identities_for_user,
    peek_email_action_token,
)
from web.i18n import get_locale, t
from web.routes import templates

router = APIRouter(tags=["account"])

AccountTab = Literal["profile", "subscription", "billing"]


def _tab(raw: str | None) -> AccountTab:
    if raw == "subscription" or raw == "billing" or raw == "profile":
        return raw
    return "profile"


def _account_url(tab: AccountTab = "profile", *, notice: str = "", error: str = "") -> str:
    url = f"/account?tab={quote(tab)}"
    if notice:
        url += f"&notice={quote(notice)}"
    if error:
        url += f"&error={quote(error)}"
    return url


def _account_page(
    request: Request,
    conn: sqlite3.Connection,
    *,
    user: UserRow,
    tab: AccountTab = "profile",
    notice: str = "",
    error: str = "",
    status_code: int = 200,
):
    identities = list_identities_for_user(conn, user.id)
    providers = [i.provider for i in identities]
    return templates.TemplateResponse(
        request,
        "account.html",
        {
            "user": user,
            "tab": tab,
            "notice": notice,
            "error": error,
            "providers": providers,
        },
        status_code=status_code,
    )


@router.get("/account", response_class=HTMLResponse)
def account_page(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[UserRow, Depends(require_user)],
    tab: str = "profile",
    notice: str = "",
    error: str = "",
):
    locale = get_locale(request)
    notice_key = notice.strip()
    notice_text = t(notice_key, locale=locale) if notice_key else ""
    error_key = error.strip()
    error_text = t(error_key, locale=locale) if error_key else ""
    return _account_page(
        request,
        conn,
        user=user,
        tab=_tab(tab),
        notice=notice_text,
        error=error_text,
    )


@router.post("/account/delete/request")
def account_delete_request(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
    confirm: str = Form(""),
):
    locale = get_locale(request)
    word = t("account.delete_confirm_word", locale=locale)
    if confirm.strip().lower() != word.lower():
        return _account_page(
            request,
            conn,
            user=user,
            tab="profile",
            error=t("err.delete_confirm", locale=locale, word=word),
            status_code=400,
        )

    token = create_email_action_token(conn, user.id, PURPOSE_DELETE_ACCOUNT)
    confirm_url = (
        f"{settings.public_base_url.rstrip('/')}"
        f"/account/confirm-delete?token={quote(token, safe='')}"
    )
    subject = t("mail.delete_subject", locale=locale)
    body = t("mail.delete_body", locale=locale, url=confirm_url)
    send_email(smtp_from_runtime(settings), to=user.email, subject=subject, body=body)
    return RedirectResponse(
        url=_account_url("profile", notice="notice.delete_email_sent"),
        status_code=303,
    )


@router.get("/account/confirm-delete", response_class=HTMLResponse)
def account_confirm_delete_page(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    user: Annotated[UserRow, Depends(require_user)],
    token: str = "",
):
    """GET only shows a confirm form — never mutates (prefetch-safe)."""
    locale = get_locale(request)
    user_id = peek_email_action_token(conn, token, PURPOSE_DELETE_ACCOUNT)
    if not user_id:
        return RedirectResponse(
            url=_account_url("profile", error="err.delete_token_invalid"),
            status_code=303,
        )
    if user_id != user.id:
        return RedirectResponse(
            url=_account_url("profile", error="err.delete_token_wrong_user"),
            status_code=303,
        )
    return templates.TemplateResponse(
        request,
        "account_confirm_delete.html",
        {
            "user": user,
            "token": token,
        },
    )


@router.post("/account/confirm-delete")
def account_confirm_delete(
    request: Request,
    conn: Annotated[sqlite3.Connection, Depends(get_conn)],
    settings: Annotated[RuntimeSettings, Depends(get_settings)],
    user: Annotated[UserRow, Depends(require_user)],
    token: str = Form(""),
):
    locale = get_locale(request)
    user_id = consume_email_action_token(conn, token, PURPOSE_DELETE_ACCOUNT)
    if not user_id:
        return RedirectResponse(
            url=_account_url("profile", error="err.delete_token_invalid"),
            status_code=303,
        )
    if user_id != user.id:
        return RedirectResponse(
            url=_account_url("profile", error="err.delete_token_wrong_user"),
            status_code=303,
        )
    target = get_user_by_id(conn, user_id)
    if not target:
        return RedirectResponse(url="/", status_code=303)

    try:
        for project in list_projects_on_disk(conn, settings.data_dir, user_id=target.id):
            delete_project_fully(conn, settings.data_dir, project.id)
    except ProjectBusyError:
        return RedirectResponse(
            url=_account_url("profile", error="err.project_busy_delete"),
            status_code=303,
        )
    delete_user(conn, target.id)
    logout_user(request)
    return RedirectResponse(url="/", status_code=303)
