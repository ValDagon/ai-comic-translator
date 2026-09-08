"""Soft quotas for enqueue (MVP multi-tenant guardrails)."""

from __future__ import annotations

import os
import sqlite3

from service import db as dbmod

# Per-project mutex: only one active job at a time.
MAX_ACTIVE_JOBS_PER_PROJECT = 1
# Soft cap across a user's projects (OpenRouter / disk).
DEFAULT_MAX_ACTIVE_JOBS_PER_USER = 5


def max_active_jobs_per_user() -> int:
    raw = (os.environ.get("COMIC_MAX_ACTIVE_JOBS_PER_USER") or "").strip()
    if not raw:
        return DEFAULT_MAX_ACTIVE_JOBS_PER_USER
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_ACTIVE_JOBS_PER_USER


def enqueue_block_reason(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    user_id: str,
    locale: str = "en",
) -> str | None:
    """Localized error if the user cannot enqueue another job, else None."""
    from web.i18n import t

    if dbmod.project_has_active_jobs(conn, project_id):
        return t("err.job_project_busy", locale=locale)
    limit = max_active_jobs_per_user()
    if dbmod.count_user_active_jobs(conn, user_id) >= limit:
        return t("err.job_user_quota", locale=locale, limit=limit)
    return None
