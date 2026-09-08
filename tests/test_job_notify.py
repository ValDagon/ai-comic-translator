"""Job-ready email notification."""

from __future__ import annotations

from service import db as dbmod
from service.job_notify import notify_job_ready
from service.paths import ProjectPaths


def test_notify_job_ready_no_smtp(api_client, monkeypatch):
    client, data_dir, get_settings = api_client
    sent: list[tuple[str, str]] = []

    def _capture(smtp, *, to, subject, body):
        sent.append((to, subject))

    monkeypatch.setattr("service.job_notify.send_email", _capture)

    pid = client.post("/projects", json={"name": "MailTest"}).json()["id"]
    ProjectPaths.for_project(data_dir, pid).ensure_dirs()

    client.post(f"/projects/{pid}/jobs", json={"kind": "render"})

    conn = dbmod.connect(get_settings().database_path)
    jobs = dbmod.list_jobs(conn, pid)
    assert jobs
    job = jobs[0]
    dbmod.finish_job(conn, job.id, ok=True)
    notify_job_ready(conn, job, get_settings())
    conn.close()

    assert sent
    assert sent[0][0] == "test@example.com"
    assert "MailTest" in sent[0][1]
