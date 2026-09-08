from pathlib import Path

from service import db as dbmod
from service.project_id import sanitize_folder_name


def test_sanitize_folder_name():
    assert sanitize_folder_name("  Issue #12  ") == "Issue #12"
    assert sanitize_folder_name("a/b") == "a－b"
    assert ".." not in sanitize_folder_name("..")


def test_create_project_uses_folder_name(tmp_path: Path):
    data_dir = tmp_path / "data"
    db_path = data_dir / "app.db"
    conn = dbmod.connect(db_path)
    dbmod.init_db(conn)
    row = dbmod.create_project(conn, "My Comic", data_dir=data_dir)
    assert row.id == "My Comic"
    assert (data_dir / "projects" / "My Comic").name == "My Comic"
    row2 = dbmod.create_project(conn, "My Comic", data_dir=data_dir)
    assert row2.id == "My Comic (2)"
    conn.close()


def test_recover_interrupted_running_jobs(tmp_path: Path):
    data_dir = tmp_path / "data"
    conn = dbmod.connect(data_dir / "app.db")
    dbmod.init_db(conn)
    p = dbmod.create_project(conn, "x", data_dir=data_dir)
    job = dbmod.create_job(conn, p.id, "render")
    conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job.id,))
    conn.commit()
    n = dbmod.recover_interrupted_running_jobs(conn)
    assert n == 1
    updated = dbmod.get_job(conn, job.id)
    assert updated.status == "failed"
    conn.close()


def test_recover_stale_running_jobs_respects_lease(tmp_path: Path):
    data_dir = tmp_path / "data"
    conn = dbmod.connect(data_dir / "app.db")
    dbmod.init_db(conn)
    p = dbmod.create_project(conn, "x", data_dir=data_dir)
    job = dbmod.create_job(conn, p.id, "render")
    conn.execute(
        """
        UPDATE jobs
        SET status = 'running',
            started_at = '2020-01-01T00:00:00+00:00',
            heartbeat_at = '2020-01-01T00:00:00+00:00'
        WHERE id = ?
        """,
        (job.id,),
    )
    conn.commit()
    n = dbmod.recover_stale_running_jobs(conn, stale_minutes=60)
    assert n == 1
    assert dbmod.get_job(conn, job.id).status == "failed"
    conn.close()
