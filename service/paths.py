"""Структура каталогов проекта в DATA_DIR."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    input_dir: Path
    clean_dir: Path
    out_dir: Path
    translations_path: Path
    fonts_dir: Path
    font_meta_path: Path

    @classmethod
    def for_project(cls, data_dir: Path, project_id: str) -> ProjectPaths:
        root = data_dir / "projects" / project_id
        return cls(
            root=root,
            input_dir=root / "input",
            clean_dir=root / "_clean",
            out_dir=root / "out",
            translations_path=root / "translations.json",
            fonts_dir=root / "fonts",
            font_meta_path=root / "font.json",
        )

    def ensure_dirs(self) -> None:
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.clean_dir.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.fonts_dir.mkdir(parents=True, exist_ok=True)
