from pathlib import Path

from service import db as dbmod
from service.db import connect, create_project, init_db
from service.paths import ProjectPaths
from service.project_cleanup import delete_project_fully, list_projects_on_disk


def test_purge_projects_without_folder(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "app.db"
    conn = connect(db_path)
    init_db(conn)
    row = create_project(conn, "Gone", data_dir=data_dir)
    ProjectPaths.for_project(data_dir, row.id).ensure_dirs()
    assert len(dbmod.list_projects(conn)) == 1

    import shutil

    shutil.rmtree(ProjectPaths.for_project(data_dir, row.id).root)
    removed = dbmod.purge_projects_without_folder(conn, data_dir)
    assert removed == [row.id]
    assert dbmod.list_projects(conn) == []


def test_list_projects_on_disk_purges(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    conn = connect(data_dir / "app.db")
    init_db(conn)
    row = create_project(conn, "Keep", data_dir=data_dir)
    ProjectPaths.for_project(data_dir, row.id).ensure_dirs()
    ghost = create_project(conn, "Ghost", data_dir=data_dir)
    ProjectPaths.for_project(data_dir, ghost.id).ensure_dirs()
    import shutil

    shutil.rmtree(ProjectPaths.for_project(data_dir, ghost.id).root)

    listed = list_projects_on_disk(conn, data_dir)
    assert [p.id for p in listed] == [row.id]


def test_delete_project_fully(tmp_path: Path):
    data_dir = tmp_path / "data"
    conn = connect(data_dir / "app.db")
    init_db(conn)
    row = create_project(conn, "Del", data_dir=data_dir)
    paths = ProjectPaths.for_project(data_dir, row.id)
    paths.ensure_dirs()
    (paths.input_dir / "a.png").write_bytes(b"x")

    assert delete_project_fully(conn, data_dir, row.id)
    assert dbmod.get_project(conn, row.id) is None
    assert not paths.root.exists()
