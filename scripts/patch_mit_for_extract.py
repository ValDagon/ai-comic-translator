#!/usr/bin/env python3
"""Patch pinned manga-image-translator for our extract pipeline.

Applied at Docker image build (see Dockerfile). Safe to re-run.

1. --save-text calls exit(-1) after a JSON dump, which aborts before inpaint
   and before local.py writes the *_translations.txt format we parse.
   With translator=original that branch is reachable (translator=none returned
   earlier and skipped it).

2. Single-image local mode checks params text_regions instead of ctx, so
   --save-text / --save-text-file never write dumps when batch_size falls
   back to 1 (one page, or the leftover odd page).
"""

from __future__ import annotations

import sys
from pathlib import Path


def patch(mit_root: Path) -> None:
    mt = mit_root / "manga_translator" / "manga_translator.py"
    local = mit_root / "manga_translator" / "mode" / "local.py"
    if not mt.is_file() or not local.is_file():
        raise SystemExit(f"MIT sources not found under {mit_root}")

    mt_text = mt.read_text(encoding="utf-8")
    old_exit = (
        'print("Don\'t continue if --save-text is used")  \n'
        "                exit(-1)  \n"
    )
    new_exit = (
        'print("Don\'t continue if --save-text is used")  \n'
        "                # patched by ai-comic-translator: do not abort extract\n"
    )
    if old_exit in mt_text:
        mt.write_text(mt_text.replace(old_exit, new_exit, 1), encoding="utf-8")
        print(f"patched exit(-1): {mt}")
    elif "patched by ai-comic-translator: do not abort extract" in mt_text:
        print(f"already patched exit(-1): {mt}")
    else:
        raise SystemExit(f"unexpected manga_translator.py save_text block in {mt}")

    local_text = local.read_text(encoding="utf-8")
    old_regions = (
        "                    if self.text_regions:\n"
        "                        self._save_text_to_file(path, ctx)\n"
    )
    new_regions = (
        "                    if ctx.text_regions:\n"
        "                        self._save_text_to_file(path, ctx)\n"
    )
    if old_regions in local_text:
        local.write_text(local_text.replace(old_regions, new_regions, 1), encoding="utf-8")
        print(f"patched text_regions check: {local}")
    elif new_regions in local_text:
        print(f"already patched text_regions check: {local}")
    else:
        raise SystemExit(f"unexpected local.py text_regions dump check in {local}")


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/app/manga-image-translator")
    patch(root)
