from pathlib import Path

from PIL import Image

from service import db as dbmod
from service.job_runner import process_one_job
from service.paths import ProjectPaths


def test_api_render_job(api_client, minimal_dir: Path):
    client, data_dir, get_settings = api_client

    r = client.post("/projects", json={"name": "test issue"})
    assert r.status_code == 200
    project_id = r.json()["id"]

    paths = ProjectPaths.for_project(data_dir, project_id)
    (paths.input_dir / "1_translations.txt").write_bytes(
        (minimal_dir / "1_translations.txt").read_bytes()
    )
    Image.new("RGB", (200, 120), "white").save(paths.input_dir / "1.jpg")
    Image.new("RGB", (200, 120), "white").save(paths.clean_dir / "1.jpg")
    paths.translations_path.write_bytes(
        (minimal_dir / "translations.json").read_bytes()
    )

    r = client.post(f"/projects/{project_id}/jobs", json={"kind": "render"})
    assert r.status_code == 200
    job_id = r.json()["id"]

    conn = dbmod.connect(get_settings().database_path)
    assert process_one_job(conn, get_settings())
    job = dbmod.get_job(conn, job_id)
    conn.close()

    assert job.status == "done"
    assert (paths.out_dir / "1.jpg").is_file()

    r = client.get(f"/projects/{project_id}/download")
    assert r.status_code == 200
    assert "zip" in r.headers["content-type"]
