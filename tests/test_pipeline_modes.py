from pathlib import Path

import pytest
from PIL import Image

from pipeline import PipelineConfig, PipelineError, PipelineMode, run_pipeline


def test_pipeline_extract_mode_calls_run_extraction_and_returns_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    called = {}

    def fake_extract(mit_repo, input_dir, clean_dir, **kwargs):
        called["ok"] = True
        called["mit"] = mit_repo

    monkeypatch.setattr("pipeline.run_extraction", fake_extract)

    mit = tmp_path / "mit"
    mit.mkdir()
    cfg = PipelineConfig(
        input_dir=tmp_path / "in",
        out_dir=tmp_path / "out",
        mode=PipelineMode.EXTRACT,
        mit_repo=mit,
    )
    result = run_pipeline(cfg, log=lambda _: None)
    assert called["ok"]
    assert called["mit"] == mit
    assert not result.pages
    assert not result.translations


def test_pipeline_full_skip_extract_uses_mock_translate_all(
    minimal_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, font_path: str
):
    input_dir = tmp_path / "in"
    clean_dir = tmp_path / "clean"
    out_dir = tmp_path / "out"
    input_dir.mkdir()
    clean_dir.mkdir()
    (input_dir / "1_translations.txt").write_bytes(
        (minimal_dir / "1_translations.txt").read_bytes()
    )
    Image.new("RGB", (200, 120), "white").save(input_dir / "1.jpg")
    Image.new("RGB", (200, 120), "white").save(clean_dir / "1.jpg")

    def fake_translate(pages, **kwargs):
        return {"1.1": "А", "1.2": "Б"}

    monkeypatch.setattr("pipeline.translate_all", fake_translate)
    monkeypatch.setattr(
        "pipeline.run_extraction",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("extract should be skipped")),
    )

    translations_out = tmp_path / "translations.json"
    cfg = PipelineConfig(
        input_dir=input_dir,
        out_dir=out_dir,
        clean_dir=clean_dir,
        mode=PipelineMode.FULL,
        skip_extract=True,
        translations_out=translations_out,
        font_path=font_path,
        font_scale=1.0,
        project_root=tmp_path,
    )
    result = run_pipeline(cfg, log=lambda _: None)
    assert result.translations == {"1.1": "А", "1.2": "Б"}
    assert translations_out.is_file()
    assert len(result.rendered_paths) == 1


def test_pipeline_skips_missing_clean_image(
    minimal_dir: Path, tmp_path: Path, font_path: str
):
    input_dir = tmp_path / "in"
    clean_dir = tmp_path / "clean"
    out_dir = tmp_path / "out"
    input_dir.mkdir()
    clean_dir.mkdir()
    (input_dir / "1_translations.txt").write_bytes(
        (minimal_dir / "1_translations.txt").read_bytes()
    )
    Image.new("RGB", (200, 120), "white").save(input_dir / "1.jpg")
    # clean image отсутствует

    cfg = PipelineConfig(
        input_dir=input_dir,
        out_dir=out_dir,
        clean_dir=clean_dir,
        mode=PipelineMode.RENDER,
        skip_extract=True,
        translations_in=minimal_dir / "translations.json",
        font_path=font_path,
        font_scale=1.0,
        project_root=tmp_path,
    )
    result = run_pipeline(cfg, log=lambda _: None)
    assert result.skipped_clean == ["1.jpg"]
    assert not result.rendered_paths


def test_pipeline_skips_corrupt_clean_image(
    minimal_dir: Path, tmp_path: Path, font_path: str
):
    """A corrupt/truncated clean image must not crash the whole render step."""
    input_dir = tmp_path / "in"
    clean_dir = tmp_path / "clean"
    out_dir = tmp_path / "out"
    input_dir.mkdir()
    clean_dir.mkdir()
    (input_dir / "1_translations.txt").write_bytes(
        (minimal_dir / "1_translations.txt").read_bytes()
    )
    Image.new("RGB", (200, 120), "white").save(input_dir / "1.jpg")
    (clean_dir / "1.jpg").write_bytes(b"not a real image")

    cfg = PipelineConfig(
        input_dir=input_dir,
        out_dir=out_dir,
        clean_dir=clean_dir,
        mode=PipelineMode.RENDER,
        skip_extract=True,
        translations_in=minimal_dir / "translations.json",
        font_path=font_path,
        font_scale=1.0,
        project_root=tmp_path,
    )
    result = run_pipeline(cfg, log=lambda _: None)
    assert result.skipped_clean == ["1.jpg"]
    assert not result.rendered_paths


def test_pipeline_translate_only_stops_before_render(
    minimal_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "1_translations.txt").write_bytes(
        (minimal_dir / "1_translations.txt").read_bytes()
    )
    Image.new("RGB", (200, 120), "white").save(input_dir / "1.jpg")

    def fail_translate(*_a, **_k):
        raise AssertionError("translate_all should not run when translations_in set")

    monkeypatch.setattr("pipeline.translate_all", fail_translate)

    out = tmp_path / "out"
    cfg = PipelineConfig(
        input_dir=input_dir,
        out_dir=out,
        mode=PipelineMode.TRANSLATE,
        skip_extract=True,
        translations_in=minimal_dir / "translations.json",
    )
    result = run_pipeline(cfg, log=lambda _: None)
    assert result.translations
    assert not result.rendered_paths
    assert not out.exists() or not any(out.iterdir()) if out.exists() else True


def test_pipeline_render_requires_translations(minimal_dir: Path, tmp_path: Path):
    input_dir = tmp_path / "in"
    clean_dir = tmp_path / "clean"
    input_dir.mkdir()
    clean_dir.mkdir()
    (input_dir / "1_translations.txt").write_bytes(
        (minimal_dir / "1_translations.txt").read_bytes()
    )
    Image.new("RGB", (10, 10), "white").save(input_dir / "1.jpg")
    Image.new("RGB", (10, 10), "white").save(clean_dir / "1.jpg")

    cfg = PipelineConfig(
        input_dir=input_dir,
        out_dir=tmp_path / "out",
        clean_dir=clean_dir,
        mode=PipelineMode.RENDER,
        skip_extract=True,
    )
    with pytest.raises(PipelineError, match="translations"):
        run_pipeline(cfg, log=lambda _: None)
