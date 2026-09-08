"""scripts/patch_mit_for_extract.py against a fake MIT tree."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_patch_module():
    root = Path(__file__).resolve().parent.parent
    path = root / "scripts" / "patch_mit_for_extract.py"
    spec = importlib.util.spec_from_file_location("patch_mit_for_extract", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _write_fake_mit(root: Path) -> None:
    mt_dir = root / "manga_translator"
    mode_dir = mt_dir / "mode"
    mode_dir.mkdir(parents=True)
    (mt_dir / "manga_translator.py").write_text(
        '            # Save translation if args.save_text is set and quit  \n'
        "            if self.save_text:  \n"
        "                input_filename = os.path.splitext(os.path.basename(self.input_files[0]))[0]  \n"
        '                with open(self._result_path(f"{input_filename}_translations.txt"), "w") as f:  \n'
        "                    json.dump(translated_sentences, f, indent=4, ensure_ascii=False)  \n"
        '                print("Don\'t continue if --save-text is used")  \n'
        "                exit(-1)  \n"
        "\n"
        "        # next\n",
        encoding="utf-8",
    )
    (mode_dir / "local.py").write_text(
        "                if self.save_text or self.save_text_file or self.prep_manual:\n"
        "                    if self.prep_manual:\n"
        "                        pass\n"
        "                    if self.text_regions:\n"
        "                        self._save_text_to_file(path, ctx)\n"
        "                return True\n",
        encoding="utf-8",
    )


def test_patch_mit_for_extract_idempotent(tmp_path: Path):
    patch = _load_patch_module().patch
    mit = tmp_path / "mit"
    _write_fake_mit(mit)
    patch(mit)
    patch(mit)  # second run must be a no-op success

    mt = (mit / "manga_translator" / "manga_translator.py").read_text(encoding="utf-8")
    local = (mit / "manga_translator" / "mode" / "local.py").read_text(encoding="utf-8")
    assert "exit(-1)" not in mt
    assert "do not abort extract" in mt
    assert "if ctx.text_regions:" in local
    assert "if self.text_regions:" not in local
