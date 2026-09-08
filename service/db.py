"""SQLite: проекты и очередь job'ов."""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from service.project_id import allocate_project_id

log = logging.getLogger("comic.db")

JobKind = str  # extract | translate | render | full
JobStatus = str  # queued | running | done | failed


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ProjectRow:
    id: str
    name: str
    created_at: str
    user_id: str | None = None


@dataclass
class JobRow:
    id: str
    project_id: str
    kind: JobKind
    status: JobStatus
    log: str
    error: str | None
    created_at: str
    started_at: str | None
    finished_at: str | None
    options_json: str = "{}"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        db_path,
        timeout=30.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    # CREATE IF NOT EXISTS не меняет старые таблицы — колонки/миграции ниже.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            plan TEXT NOT NULL DEFAULT 'internal',
            subscription_status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            log TEXT NOT NULL DEFAULT '',
            error TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id);
        CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, created_at);
        """
    )
    _migrate_users_to_oauth(conn)
    _ensure_job_options_column(conn)
    _ensure_job_heartbeat_column(conn)
    _ensure_project_user_id_column(conn)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id)"
    )
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS auth_identities (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            provider_subject TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(provider, provider_subject)
        );
        CREATE INDEX IF NOT EXISTS idx_auth_identities_user
            ON auth_identities(user_id);
        CREATE TABLE IF NOT EXISTS email_action_tokens (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            purpose TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_email_action_tokens_user
            ON email_action_tokens(user_id);
        """
    )
    conn.commit()


def _user_table_columns(conn: sqlite3.Connection) -> set[str]:
    return {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}


def _migrate_users_to_oauth(conn: sqlite3.Connection) -> None:
    """Старая схема с password_hash → wipe users/projects и OAuth-таблица users.

    Это разовая pre-launch миграция (до перехода на OAuth-only аккаунты).
    Она необратимо удаляет всех users/projects/jobs, поэтому после первого
    релиза с OAuth она НЕ должна срабатывать молча против боевых данных
    (например, при восстановлении старого бэкапа с колонкой password_hash).
    Требуем явный опт-ин через COMIC_ALLOW_LEGACY_WIPE=1, иначе отказываем
    старту, чтобы оператор заметил и разобрался, а не потерял данные тихо.
    """
    cols = _user_table_columns(conn)
    if not cols:
        return
    if "password_hash" not in cols:
        return

    allow_wipe = (os.environ.get("COMIC_ALLOW_LEGACY_WIPE") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not allow_wipe:
        raise RuntimeError(
            "Обнаружена legacy-колонка users.password_hash — эта миграция "
            "необратимо удалит ВСЕ users/projects/jobs. Если это ожидаемо "
            "(pre-launch очистка), задайте COMIC_ALLOW_LEGACY_WIPE=1 и "
            "перезапустите. Если нет — это восстановленный старый бэкап, "
            "останавливаемся, чтобы не потерять боевые данные."
        )

    log.warning(
        "legacy migration: users.password_hash found — wiping ALL users, "
        "projects and jobs (COMIC_ALLOW_LEGACY_WIPE=1 set explicitly)"
    )
    # Password-аккаунты удаляем целиком (проекты в БД тоже).
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "jobs" in tables:
        conn.execute("DELETE FROM jobs")
    if "projects" in tables:
        conn.execute("DELETE FROM projects")
    conn.execute("DELETE FROM users")
    conn.executescript(
        """
        DROP TABLE IF EXISTS users;
        CREATE TABLE users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            plan TEXT NOT NULL DEFAULT 'internal',
            subscription_status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        );
        """
    )


def _ensure_job_options_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "options_json" not in cols:
        conn.execute(
            "ALTER TABLE jobs ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'"
        )


def _ensure_job_heartbeat_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    if "heartbeat_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN heartbeat_at TEXT")


def _ensure_project_user_id_column(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "user_id" not in cols:
        conn.execute(
            "ALTER TABLE projects ADD COLUMN user_id TEXT REFERENCES users(id)"
        )


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def create_project(
    conn: sqlite3.Connection,
    name: str,
    *,
    data_dir: Path | None = None,
    user_id: str | None = None,
) -> ProjectRow:
    if data_dir is None:
        raise ValueError("data_dir обязателен для create_project")
    pid, display = allocate_project_id(name, conn, data_dir, get_project=get_project)
    created = _utc_now()
    conn.execute(
        "INSERT INTO projects (id, name, created_at, user_id) VALUES (?, ?, ?, ?)",
        (pid, display, created, user_id),
    )
    conn.commit()
    return ProjectRow(id=pid, name=display, created_at=created, user_id=user_id)


def update_project_name(
    conn: sqlite3.Connection,
    project_id: str,
    name: str,
) -> ProjectRow | None:
    display = name.strip()[:200]
    if not display:
        return None
    cur = conn.execute(
        "UPDATE projects SET name = ? WHERE id = ?",
        (display, project_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        return None
    return get_project(conn, project_id)


def list_projects(
    conn: sqlite3.Connection,
    *,
    user_id: str | None = None,
) -> list[ProjectRow]:
    if user_id is None:
        rows = conn.execute(
            "SELECT id, name, created_at, user_id FROM projects ORDER BY created_at DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, name, created_at, user_id FROM projects
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [_project_from_row(r) for r in rows]


def get_project(conn: sqlite3.Connection, project_id: str) -> ProjectRow | None:
    r = conn.execute(
        "SELECT id, name, created_at, user_id FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if not r:
        return None
    return _project_from_row(r)


def _project_from_row(r: sqlite3.Row) -> ProjectRow:
    keys = r.keys()
    return ProjectRow(
        id=r["id"],
        name=r["name"],
        created_at=r["created_at"],
        user_id=r["user_id"] if "user_id" in keys else None,
    )


def purge_projects_without_folder(
    conn: sqlite3.Connection,
    data_dir: Path,
    *,
    user_id: str | None = None,
) -> list[str]:
    """Удаляет из SQLite проекты, чья папка на диске уже снята (Finder и т.п.)."""
    removed: list[str] = []
    for row in list_projects(conn, user_id=user_id):
        root = data_dir / "projects" / row.id
        if root.is_dir():
            continue
        conn.execute("DELETE FROM projects WHERE id = ?", (row.id,))
        removed.append(row.id)
    if removed:
        conn.commit()
    return removed


def delete_project(conn: sqlite3.Connection, project_id: str) -> bool:
    cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    return cur.rowcount > 0


def project_has_active_jobs(conn: sqlite3.Connection, project_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM jobs
        WHERE project_id = ? AND status IN ('queued', 'running')
        LIMIT 1
        """,
        (project_id,),
    ).fetchone()
    return row is not None


def count_user_active_jobs(conn: sqlite3.Connection, user_id: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM jobs j
        JOIN projects p ON p.id = j.project_id
        WHERE p.user_id = ? AND j.status IN ('queued', 'running')
        """,
        (user_id,),
    ).fetchone()
    return int(row["n"] if row else 0)


def create_job(
    conn: sqlite3.Connection,
    project_id: str,
    kind: JobKind,
    *,
    options_json: str = "{}",
) -> JobRow:
    jid = str(uuid4())
    created = _utc_now()
    conn.execute(
        """
        INSERT INTO jobs (id, project_id, kind, status, log, created_at, options_json)
        VALUES (?, ?, ?, 'queued', '', ?, ?)
        """,
        (jid, project_id, kind, created, options_json),
    )
    conn.commit()
    return JobRow(
        id=jid,
        project_id=project_id,
        kind=kind,
        status="queued",
        log="",
        error=None,
        created_at=created,
        started_at=None,
        finished_at=None,
        options_json=options_json,
    )


def list_jobs(conn: sqlite3.Connection, project_id: str) -> list[JobRow]:
    rows = conn.execute(
        """
        SELECT id, project_id, kind, status, log, error, created_at, started_at, finished_at, options_json
        FROM jobs WHERE project_id = ? ORDER BY created_at DESC
        """,
        (project_id,),
    ).fetchall()
    return [_job_from_row(r) for r in rows]


def get_job(conn: sqlite3.Connection, job_id: str) -> JobRow | None:
    r = conn.execute(
        """
        SELECT id, project_id, kind, status, log, error, created_at, started_at, finished_at, options_json
        FROM jobs WHERE id = ?
        """,
        (job_id,),
    ).fetchone()
    if not r:
        return None
    return _job_from_row(r)


def _job_from_row(r: sqlite3.Row) -> JobRow:
    return JobRow(
        id=r["id"],
        project_id=r["project_id"],
        kind=r["kind"],
        status=r["status"],
        log=r["log"] or "",
        error=r["error"],
        created_at=r["created_at"],
        started_at=r["started_at"],
        finished_at=r["finished_at"],
        options_json=r["options_json"] if "options_json" in r.keys() else "{}",
    )


def append_job_log(conn: sqlite3.Connection, job_id: str, line: str) -> None:
    now = _utc_now()
    conn.execute(
        """
        UPDATE jobs
        SET log = log || ?, heartbeat_at = ?
        WHERE id = ?
        """,
        (line + "\n", now, job_id),
    )
    conn.commit()


def touch_job_heartbeat(conn: sqlite3.Connection, job_id: str) -> None:
    conn.execute(
        """
        UPDATE jobs SET heartbeat_at = ?
        WHERE id = ? AND status = 'running'
        """,
        (_utc_now(), job_id),
    )
    conn.commit()


def recover_interrupted_running_jobs(conn: sqlite3.Connection) -> int:
    """Worker startup: все running — orphans после падения этого worker."""
    finished = _utc_now()
    msg = "Worker был остановлен или job прерван. Запустите job снова."
    cur = conn.execute(
        """
        UPDATE jobs
        SET status = 'failed', error = ?, finished_at = ?
        WHERE status = 'running'
        """,
        (msg, finished),
    )
    conn.commit()
    return cur.rowcount


def recover_stale_running_jobs(
    conn: sqlite3.Connection,
    *,
    stale_minutes: int = 120,
) -> int:
    """Пометить running без heartbeat дольше stale_minutes (lease)."""
    from datetime import timedelta

    if stale_minutes < 1:
        stale_minutes = 1
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)
    ).replace(microsecond=0).isoformat()
    finished = _utc_now()
    msg = "Job stalled (lease expired). Запустите job снова."
    cur = conn.execute(
        """
        UPDATE jobs
        SET status = 'failed', error = ?, finished_at = ?
        WHERE status = 'running'
          AND COALESCE(heartbeat_at, started_at, created_at) < ?
        """,
        (msg, finished, cutoff),
    )
    conn.commit()
    return cur.rowcount


def claim_next_job(conn: sqlite3.Connection) -> JobRow | None:
    with transaction(conn):
        row = conn.execute(
            """
            SELECT id FROM jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
        if not row:
            return None
        jid = row["id"]
        started = _utc_now()
        # rowcount — строки от этого UPDATE; total_changes на долгоживущем
        # соединении воркера накопительный и ломает claim после первого job.
        cur = conn.execute(
            """
            UPDATE jobs
            SET status = 'running', started_at = ?, heartbeat_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (started, started, jid),
        )
        if cur.rowcount != 1:
            return None
    return get_job(conn, jid)


def finish_job(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    ok: bool,
    error: str | None = None,
) -> bool:
    """Завершить только running job. False если строка уже не running."""
    finished = _utc_now()
    cur = conn.execute(
        """
        UPDATE jobs
        SET status = ?, error = ?, finished_at = ?
        WHERE id = ? AND status IN ('queued', 'running')
        """,
        ("done" if ok else "failed", error, finished, job_id),
    )
    conn.commit()
    return cur.rowcount == 1
