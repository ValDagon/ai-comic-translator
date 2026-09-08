"""Синхронизация проектов между SQLite и data/projects/."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from service import db as dbmod
from service.db import ProjectRow
from service.paths import ProjectPaths


class ProjectBusyError(RuntimeError):
    """Проект нельзя удалить: есть queued/running job."""


def list_projects_on_disk(
    conn: sqlite3.Connection,
    data_dir: Path,
    *,
    user_id: str | None = None,
) -> list[ProjectRow]:
    dbmod.purge_projects_without_folder(conn, data_dir, user_id=user_id)
    return dbmod.list_projects(conn, user_id=user_id)


def get_project_on_disk(
    conn: sqlite3.Connection,
    data_dir: Path,
    project_id: str,
    *,
    user_id: str | None = None,
) -> ProjectRow | None:
    row = dbmod.get_project(conn, project_id)
    if not row:
        return None
    if user_id is not None and row.user_id != user_id:
        return None
    if ProjectPaths.for_project(data_dir, project_id).root.is_dir():
        return row
    dbmod.delete_project(conn, project_id)
    return None


def remove_project_tree(data_dir: Path, project_id: str) -> None:
    root = ProjectPaths.for_project(data_dir, project_id).root
    if root.is_dir():
        shutil.rmtree(root)


def delete_project_fully(
    conn: sqlite3.Connection,
    data_dir: Path,
    project_id: str,
    *,
    allow_active: bool = False,
) -> bool:
    if not allow_active and dbmod.project_has_active_jobs(conn, project_id):
        raise ProjectBusyError(project_id)
    if not dbmod.delete_project(conn, project_id):
        return False
    remove_project_tree(data_dir, project_id)
    return True
