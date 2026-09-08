"""Email notification when a translation job completes."""

from __future__ import annotations

import sqlite3
from urllib.parse import quote

from service.db import JobRow, get_project
from service.mailer import send_email, smtp_from_runtime
from service.runtime import RuntimeSettings
from service.users import get_user_by_id
from web.i18n import t


def notify_job_ready(
    conn: sqlite3.Connection,
    job: JobRow,
    settings: RuntimeSettings,
) -> None:
    """Send a job-ready email; no-op if SMTP is not configured."""
    project = get_project(conn, job.project_id)
    if project is None or not project.user_id:
        return
    user = get_user_by_id(conn, project.user_id)
    if user is None or not user.email:
        return

    base = (settings.public_base_url or "").rstrip("/")
    project_path = f"/projects/{quote(project.id, safe='')}/ui"
    url = f"{base}{project_path}" if base else project_path

    subject = t("mail.job_ready_subject", locale="en", name=project.name)
    body = t(
        "mail.job_ready_body",
        locale="en",
        name=project.name,
        kind=job.kind,
        url=url,
    )
    send_email(
        smtp_from_runtime(settings),
        to=user.email,
        subject=subject,
        body=body,
    )
