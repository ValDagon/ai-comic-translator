from pathlib import Path

import pytest

from config_loader import load_settings
from pipeline import PipelineConfig, PipelineError, resolve_font_path, run_pipeline


def test_load_settings_missing_returns_defaults(tmp_path: Path):
    cfg = load_settings(tmp_path / "nope.toml")
    assert cfg.openrouter.api_key is None
    assert cfg.render.font_scale is None


def test_load_settings_reads_toml(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[openrouter]\nmodel = "test/model"\npages_per_batch = 7\n\n'
        '[render]\nfont_scale = 1.5\n',
        encoding="utf-8",
    )
    cfg = load_settings(p)
    assert cfg.openrouter.model == "test/model"
    assert cfg.openrouter.pages_per_batch == 7
    assert cfg.render.font_scale == 1.5


def test_load_settings_merges_local_overlay(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[openrouter]\nmodel = "base/model"\npages_per_batch = 7\n\n'
        "[ui]\ndeveloper_mode = true\n",
        encoding="utf-8",
    )
    (tmp_path / "config.local.toml").write_text(
        '[openrouter]\napi_key = "sk-test"\nmodel = "local/model"\n',
        encoding="utf-8",
    )
    cfg = load_settings(p)
    assert cfg.openrouter.api_key == "sk-test"
    assert cfg.openrouter.model == "local/model"
    assert cfg.openrouter.pages_per_batch == 7
    assert cfg.ui.developer_mode is True


def test_load_settings_server_and_ui_sections(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[server]\ndata_dir = "data"\nmit_repo = "./manga-image-translator"\n'
        'host = "127.0.0.1"\nport = 9000\n\n'
        "[ui]\ndeveloper_mode = true\n",
        encoding="utf-8",
    )
    cfg = load_settings(p)
    assert cfg.server.data_dir == "data"
    assert cfg.server.mit_repo == "./manga-image-translator"
    assert cfg.server.port == 9000
    assert cfg.ui.developer_mode is True

    p2 = tmp_path / "config2.toml"
    p2.write_text('[ui]\ndeveloper_mode = "on"\n', encoding="utf-8")
    assert load_settings(p2).ui.developer_mode is True


def test_resolve_font_path_relative(tmp_path: Path):
    font = tmp_path / "fonts" / "a.ttf"
    font.parent.mkdir()
    font.write_bytes(b"x")
    resolved = resolve_font_path("fonts/a.ttf", tmp_path)
    assert resolved == font.resolve()


def test_resolve_font_missing_raises(tmp_path: Path):
    with pytest.raises(PipelineError):
        resolve_font_path("missing.ttf", tmp_path)


def test_run_pipeline_render_only(minimal_dir: Path, font_path: str, tmp_path: Path):
    from PIL import Image

    input_dir = tmp_path / "in"
    out_dir = tmp_path / "out"
    clean_dir = tmp_path / "clean"
    input_dir.mkdir()
    out_dir.mkdir()
    clean_dir.mkdir()

    for name in ("1_translations.txt",):
        (input_dir / name).write_bytes((minimal_dir / name).read_bytes())
    Image.new("RGB", (200, 120), "white").save(input_dir / "1.jpg")
    Image.new("RGB", (200, 120), "white").save(clean_dir / "1.jpg")

    cfg = PipelineConfig(
        input_dir=input_dir,
        out_dir=out_dir,
        clean_dir=clean_dir,
        skip_extract=True,
        translations_in=minimal_dir / "translations.json",
        font_path=font_path,
        font_scale=1.0,
        project_root=tmp_path,
    )
    result = run_pipeline(cfg, log=lambda _: None)
    assert len(result.pages) == 1
    assert len(result.rendered_paths) == 1
    assert result.rendered_paths[0].is_file()


def test_run_pipeline_strict_missing_translation(minimal_dir: Path, tmp_path: Path):
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "1.jpg").write_bytes(b"x")
    (input_dir / "1_translations.txt").write_bytes(
        (minimal_dir / "1_translations.txt").read_bytes()
    )
    partial = tmp_path / "partial.json"
    partial.write_text('{"translations": {"1.1": "only one"}}', encoding="utf-8")

    cfg = PipelineConfig(
        input_dir=input_dir,
        out_dir=tmp_path / "out",
        clean_dir=tmp_path / "clean",
        skip_extract=True,
        translations_in=partial,
        strict=True,
    )
    with pytest.raises(PipelineError, match="Нет перевода"):
        run_pipeline(cfg, log=lambda _: None)
