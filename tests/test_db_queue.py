"""Регрессии очереди job'ов (claim на долгоживущем соединении)."""

from __future__ import annotations

import threading
from pathlib import Path

from service import db as dbmod


def _setup(tmp_path: Path):
    data_dir = tmp_path / "data"
    conn = dbmod.connect(data_dir / "app.db")
    dbmod.init_db(conn)
    project = dbmod.create_project(conn, "queue", data_dir=data_dir)
    return conn, project


def test_claim_next_job_multiple_on_same_connection(tmp_path: Path):
    """Воркер держит один conn: после finish claim должен брать следующий job."""
    conn, project = _setup(tmp_path)
    j1 = dbmod.create_job(conn, project.id, "render")
    j2 = dbmod.create_job(conn, project.id, "render")
    j3 = dbmod.create_job(conn, project.id, "render")

    claimed = []
    for _ in range(3):
        job = dbmod.claim_next_job(conn)
        assert job is not None
        assert job.status == "running"
        claimed.append(job.id)
        dbmod.finish_job(conn, job.id, ok=True)

    assert claimed == [j1.id, j2.id, j3.id]
    assert dbmod.claim_next_job(conn) is None
    conn.close()


def test_claim_next_job_only_one_winner(tmp_path: Path):
    """Два соединения: один queued job должен стать running ровно один раз."""
    data_dir = tmp_path / "data"
    db_path = data_dir / "app.db"
    conn0 = dbmod.connect(db_path)
    dbmod.init_db(conn0)
    project = dbmod.create_project(conn0, "race", data_dir=data_dir)
    job = dbmod.create_job(conn0, project.id, "render")
    conn0.close()

    results: list[str | None] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        c = dbmod.connect(db_path)
        barrier.wait(timeout=5)
        claimed = dbmod.claim_next_job(c)
        results.append(claimed.id if claimed else None)
        c.close()

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0] == job.id

    check = dbmod.connect(db_path)
    row = dbmod.get_job(check, job.id)
    assert row is not None
    assert row.status == "running"
    check.close()
