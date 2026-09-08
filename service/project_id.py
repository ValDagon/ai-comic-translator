"""Имя каталога проекта (data/projects/<id>/) из названия пользователя."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path


def sanitize_folder_name(name: str) -> str:
    s = name.strip()
    s = s.replace("\0", "")
    s = s.replace("/", "－").replace("\\", "－")
    s = re.sub(r"\s+", " ", s)
    s = s.strip(". ")
    return s[:200] if s else "project"


def allocate_project_id(
    display_name: str,
    conn: sqlite3.Connection,
    data_dir: Path,
    *,
    get_project,
) -> tuple[str, str]:
    """
    Возвращает (project_id, display_name).
    project_id = имя папки на диске; при коллизии добавляется « (2)», « (3)»…
    """
    display = display_name.strip()[:200] or "project"
    base = sanitize_folder_name(display)
    candidate = base
    n = 2
    projects_root = data_dir / "projects"
    while True:
        taken_db = get_project(conn, candidate) is not None
        taken_disk = (projects_root / candidate).exists()
        if not taken_db and not taken_disk:
            return candidate, display
        candidate = f"{base} ({n})"
        n += 1
