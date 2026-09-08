"""Шрифт проекта: загрузка через UI и путь для job render."""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import ImageFont

from service.paths import ProjectPaths

FONT_SUFFIXES = {".ttf", ".otf"}
MAX_FONT_BYTES = 10 * 1024 * 1024  # 10 MiB


@dataclass
class ProjectFontInfo:
    display_name: str
    """Путь для font_path job/CLI — относительно корня репо, если возможно."""
    job_font_path: str


@dataclass
class FontUploadResult:
    ok: bool
    error: str | None = None
    info: ProjectFontInfo | None = None


def _sanitize_font_basename(name: str) -> str:
    base = Path(name.replace("\\", "/")).name
    stem = Path(base).stem or "font"
    suffix = Path(base).suffix.lower()
    if suffix not in FONT_SUFFIXES:
        suffix = ".ttf"
    stem = re.sub(r"[^\w.\- ()]", "_", stem).strip("._ ") or "font"
    return f"{stem}{suffix}"


def _validate_font_bytes(data: bytes) -> bool:
    if not data:
        return False
    try:
        ImageFont.truetype(io.BytesIO(data), size=16)
        return True
    except Exception:
        return False


def _unique_font_dest(fonts_dir: Path, basename: str) -> Path:
    dest = fonts_dir / basename
    if not dest.exists():
        return dest
    stem = Path(basename).stem
    suffix = Path(basename).suffix
    n = 2
    while True:
        candidate = fonts_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def save_project_font(
    paths: ProjectPaths,
    *,
    filename: str,
    data: bytes,
    locale: str = "en",
) -> FontUploadResult:
    from web.i18n import t

    if len(data) > MAX_FONT_BYTES:
        return FontUploadResult(
            ok=False,
            error=t(
                "err.font_too_large",
                locale=locale,
                mb=MAX_FONT_BYTES // (1024 * 1024),
            ),
        )
    suffix = Path(filename.replace("\\", "/")).suffix.lower()
    if suffix not in FONT_SUFFIXES:
        return FontUploadResult(
            ok=False,
            error=t("err.font_type", locale=locale),
        )
    if not _validate_font_bytes(data):
        return FontUploadResult(
            ok=False,
            error=t("err.font_invalid", locale=locale),
        )

    paths.ensure_dirs()
    save_as = _sanitize_font_basename(filename)
    dest = _unique_font_dest(paths.fonts_dir, save_as)
    dest.write_bytes(data)

    rel = dest.relative_to(paths.root).as_posix()
    meta = {
        "relative_path": rel,
        "original_name": Path(filename.replace("\\", "/")).name,
        "saved_as": dest.name,
    }
    paths.font_meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    info = _info_from_meta(paths, meta)
    if info is None:
        return FontUploadResult(ok=False, error=t("err.font_meta", locale=locale))
    return FontUploadResult(ok=True, info=info)


def _info_from_meta(paths: ProjectPaths, meta: dict) -> ProjectFontInfo | None:
    rel = meta.get("relative_path")
    if not rel:
        return None
    full = (paths.root / rel).resolve()
    if not full.is_file():
        return None
    display = meta.get("original_name") or meta.get("saved_as") or full.name
    return ProjectFontInfo(display_name=str(display), job_font_path=rel)


def load_project_font(paths: ProjectPaths) -> ProjectFontInfo | None:
    if not paths.font_meta_path.is_file():
        return None
    try:
        meta = json.loads(paths.font_meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(meta, dict):
        return None
    return _info_from_meta(paths, meta)


def project_font_path_for_repo(repo_root: Path, paths: ProjectPaths) -> str | None:
    """Путь шрифта проекта для поля font_path (относительно repo_root)."""
    info = load_project_font(paths)
    if not info:
        return None
    full = (paths.root / info.job_font_path).resolve()
    if not full.is_file():
        return None
    try:
        return str(full.relative_to(repo_root.resolve()))
    except ValueError:
        return str(full)
