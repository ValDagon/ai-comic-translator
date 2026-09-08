#!/usr/bin/env python3
"""Delete projects older than COMIC_PROJECT_RETENTION_DAYS (default 3). For cron on the server."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

from service import db as dbmod
from service.project_cleanup import delete_project_fully
from service.runtime import resolve_runtime


def _parse_utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)


def main() -> int:
    days = int(os.environ.get("COMIC_PROJECT_RETENTION_DAYS", "3"))
    if days < 1:
        print("COMIC_PROJECT_RETENTION_DAYS must be >= 1", file=sys.stderr)
        return 1

    settings = resolve_runtime()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    conn = dbmod.connect(settings.database_path)
    dbmod.init_db(conn)

    removed: list[str] = []
    skipped_busy: list[str] = []
    for row in dbmod.list_projects(conn):
        try:
            created = _parse_utc(row.created_at)
        except ValueError:
            continue
        if created >= cutoff:
            continue
        if dbmod.project_has_active_jobs(conn, row.id):
            skipped_busy.append(row.id)
            continue
        try:
            if delete_project_fully(conn, settings.data_dir, row.id):
                removed.append(row.id)
        except Exception as exc:
            # ProjectBusyError or rmtree issues — skip
            print(f"skip {row.id}: {exc}", file=sys.stderr)

    conn.close()
    if skipped_busy:
        print(f"Skipped {len(skipped_busy)} project(s) with active jobs: {', '.join(skipped_busy)}")
    if removed:
        print(f"Removed {len(removed)} project(s) older than {days} day(s): {', '.join(removed)}")
    else:
        print(f"No projects older than {days} day(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
