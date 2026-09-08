import io
import zipfile
from pathlib import Path

from PIL import Image

from service.upload_pages import MAX_IMAGE_BYTES, IngestResult, process_upload


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_save_folder_paths_flattened(tmp_path: Path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    result = process_upload(
        input_dir, filename="chapter1/page.png", data=_png_bytes()
    )
    assert result.saved == ["page.png"]
    assert not result.skipped


def test_rejects_non_image(tmp_path: Path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    result = process_upload(input_dir, filename="notes.txt", data=b"hello")
    assert not result.saved
    assert len(result.skipped) == 1


def test_fake_image_extension(tmp_path: Path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    result = process_upload(input_dir, filename="x.jpg", data=b"not-an-image")
    assert not result.saved
    assert "не распознан" in result.skipped[0].reason


def test_extract_zip_images(tmp_path: Path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    png = _png_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.png", png)
        zf.writestr("nested/b.png", png)
        zf.writestr("readme.txt", b"nope")
    result = process_upload(input_dir, filename="pages.zip", data=buf.getvalue())
    assert set(result.saved) == {"a.png", "b.png"}
    assert any("readme" in s.name for s in result.skipped)


def test_bad_zip(tmp_path: Path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    result = process_upload(input_dir, filename="bad.zip", data=b"not a zip")
    assert not result.saved
    assert "ZIP" in result.skipped[0].reason


def test_image_without_extension(tmp_path: Path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    png = _png_bytes()
    result = process_upload(
        input_dir,
        filename="folder/photo",
        data=png,
        content_type="image/png",
    )
    assert result.saved == ["photo.png"]


def test_skipped_for_user_warning_ignores_junk():
    from service.upload_pages import SkippedFile, skipped_for_user_warning

    skipped = [
        SkippedFile(".DS_Store", "служебный файл"),
        SkippedFile("a.txt", "допустимы только изображения"),
    ]
    assert len(skipped_for_user_warning(skipped)) == 1


def test_upload_rejects_oversized_image(tmp_path: Path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    huge = b"x" * (MAX_IMAGE_BYTES + 1)
    result = process_upload(input_dir, filename="huge.png", data=huge)
    assert not result.saved
    assert "лимит" in result.skipped[0].reason
    assert "2 MiB" in result.skipped[0].reason


def test_zip_rejects_oversized_inner_image(tmp_path: Path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("ok.png", _png_bytes())
        zf.writestr("huge.png", b"x" * (MAX_IMAGE_BYTES + 1))
    result = process_upload(input_dir, filename="pages.zip", data=buf.getvalue())
    assert result.saved == ["ok.png"]
    assert any("2 MiB" in s.reason for s in result.skipped)


def test_zip_bomb_rejected_before_decompression(tmp_path: Path, monkeypatch):
    """A ZIP declaring more uncompressed bytes than our cap must be rejected
    up front, without fully decompressing it (zf.testzip() would otherwise
    inflate every entry just to check its CRC)."""
    from service import upload_pages

    monkeypatch.setattr(upload_pages, "MAX_ZIP_UNCOMPRESSED_BYTES", 10_000)

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    buf = io.BytesIO()
    # Highly compressible payload: tiny on disk, but declares > cap uncompressed.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bomb.png", b"\x00" * 20_000)
    result = process_upload(input_dir, filename="bomb.zip", data=buf.getvalue())
    assert not result.saved
    assert "повреждённый" in result.skipped[0].reason


def test_upload_duplicate_name_gets_suffix(tmp_path: Path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    png = _png_bytes()
    assert process_upload(input_dir, filename="page.png", data=png).saved == ["page.png"]
    assert process_upload(input_dir, filename="page.png", data=png).saved == ["page_2.png"]
