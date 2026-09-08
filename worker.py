#!/usr/bin/env python3
"""Фоновый worker: забирает queued job'ы из SQLite и вызывает pipeline."""

from __future__ import annotations

import signal
import sys
import threading
import time
from pathlib import Path

from service import db as dbmod
from service.job_runner import process_one_job
from service.runtime import RuntimeSecurityError, assert_runtime_secure, resolve_runtime

# Lease window for the stale-job watchdog: jobs report a heartbeat on every
# progress line (see job_runner.log_fn); if a running job goes silent this
# long (stuck subprocess, deadlock, network hang with no timeout), the
# watchdog fails it so the queue/project mutex isn't blocked forever.
_STALE_JOB_LEASE_MINUTES = 120
_STALE_JOB_CHECK_INTERVAL_SEC = 60.0

_stop = False
_stop_event = threading.Event()


def _handle_sig(_signum, _frame):
    global _stop
    _stop = True
    _stop_event.set()


def _stale_job_watchdog(db_path: Path) -> None:
    """Runs on its own thread/connection so it keeps working even while the
    main loop is blocked synchronously inside a hung job."""
    conn = dbmod.connect(db_path)
    try:
        while not _stop_event.is_set():
            try:
                n = dbmod.recover_stale_running_jobs(
                    conn, stale_minutes=_STALE_JOB_LEASE_MINUTES
                )
                if n:
                    print(f"[watchdog] помечено stale job'ов (lease истёк): {n}", flush=True)
            except Exception as exc:  # noqa: BLE001 — watchdog must never crash silently
                print(f"[watchdog] ошибка проверки stale job'ов: {exc}", file=sys.stderr, flush=True)
            _stop_event.wait(_STALE_JOB_CHECK_INTERVAL_SEC)
    finally:
        conn.close()


def main() -> None:
    signal.signal(signal.SIGINT, _handle_sig)
    signal.signal(signal.SIGTERM, _handle_sig)

    settings = resolve_runtime()
    try:
        assert_runtime_secure(settings)
    except RuntimeSecurityError as exc:
        print(f"Worker: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    conn = dbmod.connect(settings.database_path)
    dbmod.init_db(conn)
    # Sole worker owns the queue: any leftover "running" rows are orphans.
    n = dbmod.recover_interrupted_running_jobs(conn)
    if n:
        print(f"  помечено прерванных job'ов: {n}")
    removed = dbmod.purge_projects_without_folder(conn, settings.data_dir)
    if removed:
        print(f"  убраны проекты без папки: {', '.join(removed)}")
    print(f"Worker: db={settings.database_path} data={settings.data_dir}")
    if settings.mit_repo:
        print(f"  mit_repo={settings.mit_repo}")
    else:
        print("  mit_repo не задан — job extract/full будут падать до настройки MIT_REPO")

    watchdog = threading.Thread(
        target=_stale_job_watchdog,
        args=(settings.database_path,),
        name="stale-job-watchdog",
        daemon=True,
    )
    watchdog.start()

    while not _stop:
        if process_one_job(conn, settings):
            continue
        time.sleep(settings.worker_poll_sec)

    conn.close()
    print("Worker stopped.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
