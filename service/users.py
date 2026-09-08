"""Пользователи OAuth и задел под plan / subscription (оплата позже)."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

# plan / subscription_status — задел под SaaS; сейчас всем internal + active.
DEFAULT_PLAN = "internal"
DEFAULT_SUBSCRIPTION_STATUS = "active"

PROVIDER_GOOGLE = "google"
PROVIDER_APPLE = "apple"
PROVIDER_LOCAL = "local"
VALID_PROVIDERS = frozenset({PROVIDER_GOOGLE, PROVIDER_APPLE})
LOCAL_DEV_EMAIL = "dev@local.dev"

PURPOSE_DELETE_ACCOUNT = "delete_account"
DELETE_TOKEN_TTL = timedelta(hours=1)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class UserRow:
    id: str
    email: str
    plan: str
    subscription_status: str
    created_at: str


@dataclass
class AuthIdentityRow:
    id: str
    user_id: str
    provider: str
    provider_subject: str
    created_at: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_email(email: str) -> str | None:
    e = normalize_email(email)
    if not e or len(e) > 320 or not _EMAIL_RE.match(e):
        return None
    return e


def ensure_local_dev_user(conn: sqlite3.Connection) -> UserRow:
    """Фиксированный пользователь для COMIC_APP_ENV=dev (без OAuth)."""
    existing = get_user_by_email(conn, LOCAL_DEV_EMAIL)
    if existing:
        return existing
    user = create_user(conn, LOCAL_DEV_EMAIL)
    # Identity не обязательна для local; provider local вне OAuth VALID_PROVIDERS.
    iid = str(uuid4())
    try:
        conn.execute(
            """
            INSERT INTO auth_identities
                (id, user_id, provider, provider_subject, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (iid, user.id, PROVIDER_LOCAL, "local-dev", _utc_now_iso()),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.commit()
    return user


def create_user(
    conn: sqlite3.Connection,
    email: str,
    *,
    plan: str = DEFAULT_PLAN,
    subscription_status: str = DEFAULT_SUBSCRIPTION_STATUS,
) -> UserRow:
    normalized = validate_email(email)
    if not normalized:
        raise ValueError("err.email_invalid")
    uid = str(uuid4())
    created = _utc_now_iso()
    try:
        conn.execute(
            """
            INSERT INTO users (id, email, plan, subscription_status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (uid, normalized, plan, subscription_status, created),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("err.email_taken") from exc
    return UserRow(
        id=uid,
        email=normalized,
        plan=plan,
        subscription_status=subscription_status,
        created_at=created,
    )


def get_user_by_id(conn: sqlite3.Connection, user_id: str) -> UserRow | None:
    r = conn.execute(
        """
        SELECT id, email, plan, subscription_status, created_at
        FROM users WHERE id = ?
        """,
        (user_id,),
    ).fetchone()
    return _user_from_row(r) if r else None


def get_user_by_email(conn: sqlite3.Connection, email: str) -> UserRow | None:
    normalized = normalize_email(email)
    r = conn.execute(
        """
        SELECT id, email, plan, subscription_status, created_at
        FROM users WHERE email = ?
        """,
        (normalized,),
    ).fetchone()
    return _user_from_row(r) if r else None


def list_identities_for_user(
    conn: sqlite3.Connection, user_id: str
) -> list[AuthIdentityRow]:
    rows = conn.execute(
        """
        SELECT id, user_id, provider, provider_subject, created_at
        FROM auth_identities
        WHERE user_id = ?
        ORDER BY created_at ASC
        """,
        (user_id,),
    ).fetchall()
    return [_identity_from_row(r) for r in rows]


def get_identity(
    conn: sqlite3.Connection, provider: str, provider_subject: str
) -> AuthIdentityRow | None:
    r = conn.execute(
        """
        SELECT id, user_id, provider, provider_subject, created_at
        FROM auth_identities
        WHERE provider = ? AND provider_subject = ?
        """,
        (provider, provider_subject),
    ).fetchone()
    return _identity_from_row(r) if r else None


def link_identity(
    conn: sqlite3.Connection,
    user_id: str,
    provider: str,
    provider_subject: str,
) -> AuthIdentityRow:
    if provider not in VALID_PROVIDERS:
        raise ValueError("err.oauth_provider")
    if not provider_subject or len(provider_subject) > 255:
        raise ValueError("err.oauth_subject")
    iid = str(uuid4())
    created = _utc_now_iso()
    try:
        conn.execute(
            """
            INSERT INTO auth_identities
                (id, user_id, provider, provider_subject, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (iid, user_id, provider, provider_subject, created),
        )
        conn.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("err.oauth_identity_taken") from exc
    return AuthIdentityRow(
        id=iid,
        user_id=user_id,
        provider=provider,
        provider_subject=provider_subject,
        created_at=created,
    )


def upsert_oauth_user(
    conn: sqlite3.Connection,
    *,
    provider: str,
    provider_subject: str,
    email: str,
    allow_register: bool,
) -> UserRow:
    """Найти/создать пользователя по OAuth identity (и email при первом входе)."""
    if provider not in VALID_PROVIDERS:
        raise ValueError("err.oauth_provider")
    normalized = validate_email(email)
    if not normalized:
        raise ValueError("err.email_invalid")

    identity = get_identity(conn, provider, provider_subject)
    if identity:
        user = get_user_by_id(conn, identity.user_id)
        if not user:
            raise ValueError("err.user_not_found")
        return user

    existing = get_user_by_email(conn, normalized)
    if existing:
        link_identity(conn, existing.id, provider, provider_subject)
        return existing

    if not allow_register:
        raise ValueError("err.register_closed")

    user = create_user(conn, normalized)
    link_identity(conn, user.id, provider, provider_subject)
    return user


def delete_user(conn: sqlite3.Connection, user_id: str) -> bool:
    cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return cur.rowcount > 0


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_email_action_token(
    conn: sqlite3.Connection,
    user_id: str,
    purpose: str,
    *,
    ttl: timedelta = DELETE_TOKEN_TTL,
) -> str:
    """Создаёт одноразовый токен; возвращает plaintext для ссылки."""
    token = secrets.token_urlsafe(32)
    tid = str(uuid4())
    now = _utc_now()
    conn.execute(
        """
        DELETE FROM email_action_tokens
        WHERE user_id = ? AND purpose = ? AND used_at IS NULL
        """,
        (user_id, purpose),
    )
    conn.execute(
        """
        INSERT INTO email_action_tokens
            (id, user_id, purpose, token_hash, expires_at, used_at, created_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            tid,
            user_id,
            purpose,
            _hash_token(token),
            (now + ttl).isoformat(),
            now.isoformat(),
        ),
    )
    conn.commit()
    return token


def peek_email_action_token(
    conn: sqlite3.Connection,
    token: str,
    purpose: str,
) -> str | None:
    """Проверка токена без consume (для GET confirm page)."""
    if not token:
        return None
    now = _utc_now_iso()
    r = conn.execute(
        """
        SELECT user_id, expires_at, used_at
        FROM email_action_tokens
        WHERE token_hash = ? AND purpose = ?
        """,
        (_hash_token(token), purpose),
    ).fetchone()
    if not r or r["used_at"] or r["expires_at"] < now:
        return None
    return str(r["user_id"])


def consume_email_action_token(
    conn: sqlite3.Connection,
    token: str,
    purpose: str,
) -> str | None:
    """Возвращает user_id при успехе, иначе None. Помечает токен использованным."""
    if not token:
        return None
    now = _utc_now_iso()
    r = conn.execute(
        """
        SELECT id, user_id, expires_at, used_at
        FROM email_action_tokens
        WHERE token_hash = ? AND purpose = ?
        """,
        (_hash_token(token), purpose),
    ).fetchone()
    if not r:
        return None
    if r["used_at"]:
        return None
    if r["expires_at"] < now:
        return None
    cur = conn.execute(
        """
        UPDATE email_action_tokens
        SET used_at = ?
        WHERE id = ? AND used_at IS NULL
        """,
        (now, r["id"]),
    )
    conn.commit()
    if cur.rowcount != 1:
        return None
    return str(r["user_id"])


def _user_from_row(r: sqlite3.Row) -> UserRow:
    return UserRow(
        id=r["id"],
        email=r["email"],
        plan=r["plan"],
        subscription_status=r["subscription_status"],
        created_at=r["created_at"],
    )


def _identity_from_row(r: sqlite3.Row) -> AuthIdentityRow:
    return AuthIdentityRow(
        id=r["id"],
        user_id=r["user_id"],
        provider=r["provider"],
        provider_subject=r["provider_subject"],
        created_at=r["created_at"],
    )
