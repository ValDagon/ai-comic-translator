"""Public site URL behind reverse proxies (Cloud Run, Caddy)."""

from __future__ import annotations

import os

from starlette.requests import Request

from service.runtime import RuntimeSettings


def external_base_url(request: Request, settings: RuntimeSettings) -> str:
    """
    Base URL for OAuth redirects and absolute links.

    Prefer an explicit public URL (COMIC_PUBLIC_BASE_URL / settings) so clients
    cannot poison OAuth redirect_uri via X-Forwarded-Host. Fall back to
    forwarded headers only when no explicit URL is configured, then request URL.
    """
    explicit = (os.environ.get("COMIC_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if not explicit:
        explicit = (settings.public_base_url or "").strip().rstrip("/")
    # Ignore placeholder loopback public_base_url when behind a real host.
    if explicit and not _is_loopback_url(explicit):
        return explicit

    host = (request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    if host:
        if not proto:
            proto = "https"
        return f"{proto}://{host}".rstrip("/")

    if explicit:
        return explicit

    base = str(request.base_url).rstrip("/")
    if base and "127.0.0.1" not in base and "0.0.0.0" not in base:
        return base

    return (settings.public_base_url or base).rstrip("/")


def _is_loopback_url(url: str) -> bool:
    lower = url.lower()
    return (
        "://127.0.0.1" in lower
        or "://localhost" in lower
        or "://[::1]" in lower
        or "://0.0.0.0" in lower
    )
