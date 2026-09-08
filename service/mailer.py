"""Отправка писем: SMTP из env или лог URL (dev / без сервиса)."""

from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

log = logging.getLogger("comic.mailer")


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    username: str
    password: str
    from_addr: str
    use_tls: bool = True

    @property
    def configured(self) -> bool:
        return bool(self.host and self.from_addr)


def smtp_from_runtime(settings) -> SmtpSettings:
    """Build SMTP settings from RuntimeSettings."""
    return SmtpSettings(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=settings.smtp_username,
        password=settings.smtp_password,
        from_addr=settings.smtp_from,
        use_tls=settings.smtp_use_tls,
    )


def send_email(
    smtp: SmtpSettings,
    *,
    to: str,
    subject: str,
    body: str,
) -> None:
    if not smtp.configured:
        log.info(
            "mailer: SMTP not configured; email to=%s subject=%s\n%s",
            to,
            subject,
            _redact_email_body(body),
        )
        return

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp.from_addr
    msg["To"] = to
    msg.set_content(body)

    with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as client:
        if smtp.use_tls:
            client.starttls()
        if smtp.username:
            client.login(smtp.username, smtp.password)
        client.send_message(msg)


def _redact_email_body(body: str) -> str:
    """Не пишем полные delete-токены в application logs."""
    import re

    return re.sub(
        r"(confirm-delete\?token=)([^\s&]+)",
        lambda m: m.group(1) + m.group(2)[:8] + "…",
        body,
    )
