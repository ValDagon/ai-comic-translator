"""API: health, ошибки, upload, list jobs."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from service import db as dbmod
from service.job_runner import process_one_job
from service.paths import ProjectPaths


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_api_health_ok(api_client):
    client, *_ = api_client
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_api_get_project_404(api_client):
    client, *_ = api_client
    assert client.get("/projects/no-such").status_code == 404


def test_api_delete_project_404(api_client):
    client, *_ = api_client
    assert client.delete("/projects/no-such").status_code == 404


def test_api_list_projects_after_create(api_client):
    client, *_ = api_client
    r = client.post("/projects", json={"name": "listed"})
    assert r.status_code == 200
    pid = r.json()["id"]
    listed = client.get("/projects").json()
    assert any(p["id"] == pid for p in listed)


def test_api_upload_pages_saves_and_rejects_junk(api_client):
    client, data_dir, _ = api_client
    pid = client.post("/projects", json={"name": "up"}).json()["id"]
    files = [
        ("files", ("page.png", _png_bytes(), "image/png")),
        ("files", ("notes.txt", b"hello", "text/plain")),
    ]
    r = client.post(f"/projects/{pid}/pages", files=files)
    assert r.status_code == 200
    body = r.json()
    assert "page.png" in body["saved"]
    assert body["skipped"]
    paths = ProjectPaths.for_project(data_dir, pid)
    assert (paths.input_dir / "page.png").is_file()


def test_api_enqueue_invalid_options_returns_400(api_client):
    client, *_ = api_client
    pid = client.post("/projects", json={"name": "opts"}).json()["id"]
    r = client.post(
        f"/projects/{pid}/jobs",
        json={"kind": "extract", "options": {"skip_extract": True}},
    )
    assert r.status_code == 400


def test_api_get_job_and_list_jobs(api_client, minimal_dir: Path):
    client, data_dir, get_settings = api_client
    pid = client.post("/projects", json={"name": "jobs"}).json()["id"]
    paths = ProjectPaths.for_project(data_dir, pid)
    (paths.input_dir / "1_translations.txt").write_bytes(
        (minimal_dir / "1_translations.txt").read_bytes()
    )
    Image.new("RGB", (200, 120), "white").save(paths.input_dir / "1.jpg")
    Image.new("RGB", (200, 120), "white").save(paths.clean_dir / "1.jpg")
    paths.translations_path.write_bytes(
        (minimal_dir / "translations.json").read_bytes()
    )

    r = client.post(f"/projects/{pid}/jobs", json={"kind": "render"})
    assert r.status_code == 200
    job_id = r.json()["id"]
    assert r.json()["status"] == "queued"

    listed = client.get(f"/projects/{pid}/jobs").json()
    assert len(listed) == 1
    assert listed[0]["id"] == job_id

    one = client.get(f"/projects/{pid}/jobs/{job_id}").json()
    assert one["status"] == "queued"

    conn = dbmod.connect(get_settings().database_path)
    assert process_one_job(conn, get_settings())
    conn.close()

    done = client.get(f"/projects/{pid}/jobs/{job_id}").json()
    assert done["status"] == "done"


def test_api_download_empty_out_404(api_client):
    client, data_dir, _ = api_client
    pid = client.post("/projects", json={"name": "empty-dl"}).json()["id"]
    ProjectPaths.for_project(data_dir, pid).ensure_dirs()
    assert client.get(f"/projects/{pid}/download").status_code == 404


def test_api_download_cyrillic_project_id(api_client):
    """Non-ASCII project id must not 500 on Content-Disposition."""
    client, data_dir, _ = api_client
    pid = client.post("/projects", json={"name": "Новый выпуск"}).json()["id"]
    assert pid == "Новый выпуск"
    out = ProjectPaths.for_project(data_dir, pid).out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "1.jpg").write_bytes(b"fake-jpg")

    r = client.get(f"/projects/{pid}/download")
    assert r.status_code == 200
    assert "zip" in r.headers["content-type"]
    cd = r.headers["content-disposition"]
    assert "filename=" in cd
    assert "filename*=UTF-8''" in cd
    # Header must be latin-1 encodable (Starlette / HTTP).
    cd.encode("latin-1")


def test_api_get_job_404(api_client):
    client, *_ = api_client
    pid = client.post("/projects", json={"name": "noj"}).json()["id"]
    assert client.get(f"/projects/{pid}/jobs/missing-id").status_code == 404


def test_api_download_translations(api_client, minimal_dir: Path):
    client, data_dir, _ = api_client
    pid = client.post("/projects", json={"name": "tr"}).json()["id"]
    paths = ProjectPaths.for_project(data_dir, pid)
    paths.translations_path.write_bytes(
        (minimal_dir / "translations.json").read_bytes()
    )
    r = client.get(f"/projects/{pid}/download/translations")
    assert r.status_code == 200
    assert "json" in r.headers["content-type"]
    assert r.content


def test_api_download_translations_404(api_client):
    client, data_dir, _ = api_client
    pid = client.post("/projects", json={"name": "no-tr"}).json()["id"]
    ProjectPaths.for_project(data_dir, pid).ensure_dirs()
    assert client.get(f"/projects/{pid}/download/translations").status_code == 404


def test_api_serve_out_page(api_client):
    client, data_dir, _ = api_client
    pid = client.post("/projects", json={"name": "preview"}).json()["id"]
    out = ProjectPaths.for_project(data_dir, pid).out_dir
    out.mkdir(parents=True, exist_ok=True)
    (out / "01.png").write_bytes(_png_bytes())
    r = client.get(f"/projects/{pid}/out/01.png")
    assert r.status_code == 200
    assert "image" in r.headers["content-type"]
    assert client.get(f"/projects/{pid}/out/../input/x").status_code == 404
