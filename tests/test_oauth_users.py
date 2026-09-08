"""OAuth upsert без реальных Google/Apple."""

from pathlib import Path

import pytest

from service import db as dbmod
from service.users import (
    PROVIDER_APPLE,
    PROVIDER_GOOGLE,
    get_identity,
    get_user_by_email,
    list_identities_for_user,
    upsert_oauth_user,
)


@pytest.fixture
def conn(tmp_path: Path):
    c = dbmod.connect(tmp_path / "app.db")
    dbmod.init_db(c)
    yield c
    c.close()


def test_upsert_creates_user_and_identity(conn):
    user = upsert_oauth_user(
        conn,
        provider=PROVIDER_GOOGLE,
        provider_subject="g-1",
        email="a@example.com",
        allow_register=True,
    )
    assert user.email == "a@example.com"
    ident = get_identity(conn, PROVIDER_GOOGLE, "g-1")
    assert ident is not None
    assert ident.user_id == user.id


def test_upsert_same_identity_returns_same_user(conn):
    first = upsert_oauth_user(
        conn,
        provider=PROVIDER_GOOGLE,
        provider_subject="g-1",
        email="a@example.com",
        allow_register=True,
    )
    second = upsert_oauth_user(
        conn,
        provider=PROVIDER_GOOGLE,
        provider_subject="g-1",
        email="other@example.com",
        allow_register=True,
    )
    assert second.id == first.id
    assert second.email == "a@example.com"


def test_upsert_links_second_provider_by_email(conn):
    first = upsert_oauth_user(
        conn,
        provider=PROVIDER_GOOGLE,
        provider_subject="g-1",
        email="a@example.com",
        allow_register=True,
    )
    second = upsert_oauth_user(
        conn,
        provider=PROVIDER_APPLE,
        provider_subject="a-1",
        email="a@example.com",
        allow_register=True,
    )
    assert second.id == first.id
    providers = {i.provider for i in list_identities_for_user(conn, first.id)}
    assert providers == {PROVIDER_GOOGLE, PROVIDER_APPLE}


def test_upsert_register_closed(conn):
    with pytest.raises(ValueError, match="err.register_closed"):
        upsert_oauth_user(
            conn,
            provider=PROVIDER_GOOGLE,
            provider_subject="g-1",
            email="a@example.com",
            allow_register=False,
        )
    assert get_user_by_email(conn, "a@example.com") is None


def _make_legacy_password_db(db_path: Path) -> None:
    import sqlite3

    raw = sqlite3.connect(db_path)
    raw.executescript(
        """
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'internal',
            subscription_status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        );
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            user_id TEXT
        );
        INSERT INTO users VALUES
            ('u1', 'old@example.com', 'scrypt$1', 'internal', 'active', '2020-01-01T00:00:00');
        INSERT INTO projects VALUES ('p1', 'Old', '2020-01-01T00:00:00', 'u1');
        """
    )
    raw.commit()
    raw.close()


def test_migrate_refuses_silent_wipe_without_opt_in(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("COMIC_ALLOW_LEGACY_WIPE", raising=False)
    db_path = tmp_path / "legacy.db"
    _make_legacy_password_db(db_path)

    conn = dbmod.connect(db_path)
    with pytest.raises(RuntimeError, match="COMIC_ALLOW_LEGACY_WIPE"):
        dbmod.init_db(conn)
    # Данные не тронуты — миграция отказала старту, а не удалила молча.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    assert "password_hash" in cols
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    conn.close()


def test_migrate_wipes_password_users(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("COMIC_ALLOW_LEGACY_WIPE", "1")
    db_path = tmp_path / "legacy.db"
    _make_legacy_password_db(db_path)

    conn = dbmod.connect(db_path)
    dbmod.init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    assert "password_hash" not in cols
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 0
    assert conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='auth_identities'"
    ).fetchone()
    conn.close()
