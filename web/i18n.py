"""UI localization: cookie + Accept-Language, JSON catalogs, Jinja helpers."""

from __future__ import annotations

import json
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = ROOT / "locales"

SUPPORTED_LOCALES = ("ru", "en", "ja", "ko")
DEFAULT_LOCALE = "en"
LOCALE_COOKIE = "comic_locale"
LOCALE_COOKIE_MAX_AGE = 60 * 60 * 24 * 365

LOCALE_LABELS = {
    "ru": "RU",
    "en": "EN",
    "ja": "日本語",
    "ko": "한국어",
}

router = APIRouter(tags=["locale"])


def normalize_locale(code: str | None) -> str | None:
    if not code:
        return None
    raw = code.strip().lower().replace("_", "-")
    if raw in SUPPORTED_LOCALES:
        return raw
    primary = raw.split("-", 1)[0]
    if primary in SUPPORTED_LOCALES:
        return primary
    # common aliases
    if primary == "jp":
        return "ja"
    if primary == "kr":
        return "ko"
    return None


def parse_accept_language(header: str | None) -> str | None:
    if not header:
        return None
    parts: list[tuple[str, float]] = []
    for item in header.split(","):
        item = item.strip()
        if not item:
            continue
        lang, _, rest = item.partition(";")
        q = 1.0
        if rest.strip().startswith("q="):
            try:
                q = float(rest.strip()[2:])
            except ValueError:
                q = 0.0
        parts.append((lang.strip(), q))
    parts.sort(key=lambda x: -x[1])
    for lang, _ in parts:
        found = normalize_locale(lang)
        if found:
            return found
    return None


def resolve_locale(request: Request) -> str:
    cookie = normalize_locale(request.cookies.get(LOCALE_COOKIE))
    if cookie:
        return cookie
    from_header = parse_accept_language(request.headers.get("accept-language"))
    if from_header:
        return from_header
    return DEFAULT_LOCALE


def get_locale(request: Request) -> str:
    locale = getattr(request.state, "locale", None)
    if isinstance(locale, str) and locale in SUPPORTED_LOCALES:
        return locale
    return resolve_locale(request)


@lru_cache(maxsize=8)
def _load_catalog(locale: str) -> dict[str, str]:
    path = LOCALES_DIR / f"{locale}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def t(key: str, locale: str = DEFAULT_LOCALE, **kwargs: Any) -> str:
    """Translate key; fall back to en, then key itself. Supports str.format kwargs."""
    loc = normalize_locale(locale) or DEFAULT_LOCALE
    catalog = _load_catalog(loc)
    text = catalog.get(key)
    if text is None and loc != "en":
        text = _load_catalog("en").get(key)
    if text is None and loc != "ru":
        text = _load_catalog("ru").get(key)
    if text is None:
        text = key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


def clear_catalog_cache() -> None:
    _load_catalog.cache_clear()


class LocaleMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        request.state.locale = resolve_locale(request)
        return await call_next(request)


def _safe_next(raw: str | None) -> str:
    if not raw:
        return "/"
    path = unquote(raw)
    if not path.startswith("/") or path.startswith("//"):
        return "/"
    return path


@router.post("/locale")
async def set_locale(request: Request):
    form = await request.form()
    lang = normalize_locale(str(form.get("lang") or "")) or DEFAULT_LOCALE
    nxt = _safe_next(str(form.get("next") or request.headers.get("referer") or "/"))
    # Avoid open redirect via absolute referer: only path
    if nxt.startswith("http"):
        nxt = "/"
    response = RedirectResponse(url=nxt, status_code=303)
    response.set_cookie(
        key=LOCALE_COOKIE,
        value=lang,
        max_age=LOCALE_COOKIE_MAX_AGE,
        httponly=False,
        samesite="lax",
        path="/",
    )
    return response


def locale_context(request: Request) -> dict[str, Any]:
    from service.user_log import kind_label, status_label, user_facing_log

    locale = get_locale(request)
    translate: Callable[..., str] = partial(t, locale=locale)
    return {
        "_": translate,
        "locale": locale,
        "locale_choices": [
            {"code": code, "label": LOCALE_LABELS[code]} for code in SUPPORTED_LOCALES
        ],
        "i18n_js": {
            "filesSelected": translate("js.files_selected"),
            "uploadManyConfirm": translate("js.upload_many_confirm"),
        },
        "kind_label": lambda kind: kind_label(kind, locale=locale),
        "status_label": lambda status: status_label(status, locale=locale),
        "format_user_log": lambda j: user_facing_log(
            j.log, status=j.status, kind=j.kind, error=j.error, locale=locale
        ),
    }


class I18nJinja2Templates(Jinja2Templates):
    """Inject _ / locale into every TemplateResponse context."""

    def TemplateResponse(self, request: Request, name: str, context: dict[str, Any] | None = None, **kwargs: Any):
        ctx = dict(context or {})
        for key, value in locale_context(request).items():
            ctx.setdefault(key, value)
        ctx.setdefault("request", request)
        return super().TemplateResponse(request, name, ctx, **kwargs)
