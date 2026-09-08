"""Purge projects older than COMIC_PROJECT_RETENTION_DAYS."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

from service import db as dbmod


def _load_purge_module():
    root = Path(__file__).resolve().parent.parent
    path = root / "scripts" / "purge_stale_projects.py"
    spec = importlib.util.spec_from_file_location("purge_stale_projects", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_purge_stale_projects_removes_old(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("COMIC_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COMIC_DATABASE", str(tmp_path / "data" / "app.db"))
    monkeypatch.setenv("COMIC_PROJECT_RETENTION_DAYS", "3")
    monkeypatch.setenv("COMIC_APP_ENV", "dev")

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    conn = dbmod.connect(data_dir / "app.db")
    dbmod.init_db(conn)

    old_ts = (datetime.now(timezone.utc) - timedelta(days=5)).replace(microsecond=0).isoformat()
    conn.execute(
        "INSERT INTO projects (id, name, created_at, user_id) VALUES (?, ?, ?, ?)",
        ("old1", "Old", old_ts, None),
    )
    conn.commit()
    (data_dir / "projects" / "old1" / "input").mkdir(parents=True)
    (data_dir / "projects" / "old1" / "input" / "1.png").write_bytes(b"x")

    new_ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute(
        "INSERT INTO projects (id, name, created_at, user_id) VALUES (?, ?, ?, ?)",
        ("new1", "New", new_ts, None),
    )
    conn.commit()
    (data_dir / "projects" / "new1").mkdir()

    purge_stale_projects = _load_purge_module()
    assert purge_stale_projects.main() == 0
    assert dbmod.get_project(conn, "old1") is None
    assert dbmod.get_project(conn, "new1") is not None
    assert not (data_dir / "projects" / "old1").exists()
    conn.close()
