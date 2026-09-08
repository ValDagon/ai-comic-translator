"""Миграция старой SQLite без projects.user_id."""

from pathlib import Path

import sqlite3

from service import db as dbmod


def test_init_db_adds_user_id_to_legacy_projects(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO projects (id, name, created_at) VALUES ('p1', 'Old', '2020-01-01T00:00:00');
        """
    )
    conn.commit()
    conn.close()

    conn = dbmod.connect(db_path)
    dbmod.init_db(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    assert "user_id" in cols
    row = dbmod.get_project(conn, "p1")
    assert row is not None
    assert row.user_id is None
    conn.close()
