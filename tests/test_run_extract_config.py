"""run_extract: сборка MIT config без subprocess."""

from __future__ import annotations

import json
from pathlib import Path

import run_extract


def test_mit_extractor_config_includes_renderer(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "mit_extract_config.toml"
    cfg.write_text(
        '[translator]\ntranslator = "original"\ntarget_lang = "RUS"\n'
        "no_text_lang_skip = true\n\n[render]\nrenderer = \"none\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_extract, "_MIT_CONFIG", cfg)
    out = run_extract._mit_extractor_config()
    assert out["translator"]["translator"] == "original"
    assert out["render"]["renderer"] == "none"


def test_run_extraction_builds_mit_config(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "mit_extract_config.toml"
    cfg.write_text(
        '[translator]\ntranslator = "original"\ntarget_lang = "RUS"\n'
        "no_text_lang_skip = true\n\n[render]\nrenderer = \"none\"\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_extract, "_MIT_CONFIG", cfg)

    mit_repo = tmp_path / "mit"
    mit_repo.mkdir()
    input_dir = tmp_path / "in"
    clean_dir = tmp_path / "clean"
    input_dir.mkdir()
    # Empty input is fine: post-check only fails when images exist without dumps.

    captured: dict = {}

    class FakeProc:
        stdout = iter(["ok\n"])

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            pass

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        # config file is still present during Popen
        config_path = Path(cmd[cmd.index("--config-file") + 1])
        captured["config"] = json.loads(config_path.read_text(encoding="utf-8"))
        return FakeProc()

    monkeypatch.setattr(run_extract.subprocess, "Popen", fake_popen)

    run_extract.run_extraction(mit_repo, input_dir, clean_dir, log=lambda _: None)

    assert captured["cwd"] == str(mit_repo)
    assert captured["config"]["render"]["renderer"] == "none"
    assert captured["config"]["translator"]["translator"] == "original"
    assert "--save-text" not in captured["cmd"]
    assert "--save-text-file" in captured["cmd"]
    assert (
        captured["cmd"][captured["cmd"].index("--save-text-file") + 1]
        == run_extract._MIT_SAVE_TEXT_FILE_MARKER
    )
    assert captured["cmd"][captured["cmd"].index("--batch-size") + 1] == "2"


def test_run_extraction_kills_hung_subprocess(monkeypatch, tmp_path: Path):
    """No output for the idle window ⇒ raise + kill, never block forever."""
    cfg = tmp_path / "mit_extract_config.toml"
    cfg.write_text(
        '[translator]\ntranslator = "original"\n[render]\nrenderer = "none"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(run_extract, "_MIT_CONFIG", cfg)
    monkeypatch.setattr(run_extract, "_MIT_IDLE_TIMEOUT_SEC", 0.05)

    mit_repo = tmp_path / "mit"
    mit_repo.mkdir()
    input_dir = tmp_path / "in"
    clean_dir = tmp_path / "clean"
    input_dir.mkdir()

    class HangingStdout:
        def __iter__(self):
            return self

        def __next__(self):
            import time

            time.sleep(1000)  # never actually reached in the test's lifetime

    class FakeProc:
        stdout = HangingStdout()
        killed = False

        def wait(self, timeout=None):
            if not FakeProc.killed:
                raise AssertionError("wait() should not block before kill()")
            return -9

        def poll(self):
            return None if not FakeProc.killed else -9

        def kill(self):
            FakeProc.killed = True

    monkeypatch.setattr(
        run_extract.subprocess,
        "Popen",
        lambda *a, **k: FakeProc(),
    )

    try:
        run_extract.run_extraction(mit_repo, input_dir, clean_dir, log=lambda _: None)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "завис" in str(e)
    assert FakeProc.killed is True


def test_run_extraction_raises_when_no_translation_dumps(monkeypatch, tmp_path: Path):
    cfg = tmp_path / "mit_extract_config.toml"
    cfg.write_text(
        '[translator]\ntranslator = "original"\n[render]\nrenderer = "none"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(run_extract, "_MIT_CONFIG", cfg)

    mit_repo = tmp_path / "mit"
    mit_repo.mkdir()
    input_dir = tmp_path / "in"
    clean_dir = tmp_path / "clean"
    input_dir.mkdir()
    (input_dir / "1.jpg").write_bytes(b"fake")

    class FakeProc:
        stdout = iter(["ok\n"])

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(
        run_extract.subprocess,
        "Popen",
        lambda *a, **k: FakeProc(),
    )

    try:
        run_extract.run_extraction(mit_repo, input_dir, clean_dir, log=lambda _: None)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as e:
        assert "*_translations.txt" in str(e)
