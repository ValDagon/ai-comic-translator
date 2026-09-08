"""Сохранение страниц в project input/ (файлы, папка, zip) с валидацией."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
ARCHIVE_SUFFIXES = {".zip"}

# Лимиты против DoS при случайной экспозиции localhost API.
MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MiB на одну картинку
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB на ZIP
MAX_PAGES_PER_UPLOAD = 200  # максимум сохранённых страниц за один process_upload
MAX_IMAGE_PIXELS = 40_000_000  # ~6300² — защита от pixel bomb
# Верхняя граница суммарного РАСПАКОВАННОГО объёма архива — защита от zip-бомб
# (высокий коэффициент сжатия внутри маленького файла ≤MAX_UPLOAD_BYTES).
# Проверяется по заявленным zipfile.ZipInfo.file_size ДО любой распаковки
# (включая zf.testzip(), который иначе сам распаковывает всё целиком).
MAX_ZIP_UNCOMPRESSED_BYTES = MAX_PAGES_PER_UPLOAD * MAX_IMAGE_BYTES


def _size_limit_reason(limit_bytes: int) -> str:
    return f"файл больше лимита {limit_bytes // (1024 * 1024)} MiB"


@dataclass
class SkippedFile:
    name: str
    reason: str


@dataclass
class IngestResult:
    saved: list[str] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)

    def merge(self, other: IngestResult) -> None:
        self.saved.extend(other.saved)
        self.skipped.extend(other.skipped)


_JUNK_SKIP_REASONS = frozenset({
    "служебный файл",
    "служебный файл в архиве",
})


def skipped_for_user_warning(skipped: list[SkippedFile]) -> list[SkippedFile]:
    """Не пугаем пользователя из‑за .DS_Store и __MACOSX."""
    return [s for s in skipped if s.reason not in _JUNK_SKIP_REASONS]


def is_page_filename(name: str) -> bool:
    return Path(name).suffix.lower() in IMAGE_SUFFIXES


def is_zip_filename(name: str) -> bool:
    return Path(name).suffix.lower() in ARCHIVE_SUFFIXES


def _is_junk_path(rel: Path) -> bool:
    if rel.name.startswith(".") or rel.name == ".DS_Store":
        return True
    return "__MACOSX" in rel.parts or ".DS_Store" in rel.parts


def _validate_image_bytes(data: bytes) -> bool:
    if not data:
        return False
    try:
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            if w <= 0 or h <= 0 or (w * h) > MAX_IMAGE_PIXELS:
                return False
            im.load()
        return True
    except Exception:
        return False


def _extension_from_image_bytes(data: bytes) -> str | None:
    try:
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            if w <= 0 or h <= 0 or (w * h) > MAX_IMAGE_PIXELS:
                return None
            im.load()
            fmt = (im.format or "").upper()
    except Exception:
        return None
    return {
        "JPEG": ".jpg",
        "PNG": ".png",
        "WEBP": ".webp",
    }.get(fmt)


def _resolve_image_basename(
    filename: str,
    data: bytes,
    content_type: str | None,
) -> str | None:
    name = Path(filename.replace("\\", "/")).name
    if is_page_filename(name):
        return name
    ext = _extension_from_image_bytes(data)
    if ext:
        stem = Path(name).stem or "page"
        return f"{stem}{ext}"
    if content_type and content_type.startswith("image/"):
        sub = content_type.split("/")[-1].split(";")[0].lower()
        ext = {"jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "webp": ".webp"}.get(
            sub, ".jpg"
        )
        stem = Path(name).stem or "page"
        return f"{stem}{ext}"
    return None


def _validate_zip_bytes(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            # Reject bombs by declared size BEFORE decompressing anything —
            # zf.testzip() below fully inflates every entry to check its CRC,
            # so it must never run first on unbounded/attacker-controlled sizes.
            total_uncompressed = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                total_uncompressed += info.file_size
                if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                    return False
            if zf.testzip() is not None:
                return False
        return True
    except (zipfile.BadZipFile, OSError):
        return False


def _unique_dest(input_dir: Path, basename: str) -> Path:
    dest = input_dir / basename
    if not dest.exists():
        return dest
    stem = Path(basename).stem
    suffix = Path(basename).suffix
    n = 2
    while True:
        candidate = input_dir / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def process_upload(
    input_dir: Path,
    *,
    filename: str,
    data: bytes,
    content_type: str | None = None,
) -> IngestResult:
    """Один файл из multipart (имя может быть с путём при загрузке папки)."""
    result = IngestResult()
    rel = Path(filename.replace("\\", "/"))
    display = str(rel)

    if _is_junk_path(rel):
        result.skipped.append(SkippedFile(display, "служебный файл"))
        return result

    if is_zip_filename(rel.name):
        if len(data) > MAX_UPLOAD_BYTES:
            result.skipped.append(SkippedFile(display, _size_limit_reason(MAX_UPLOAD_BYTES)))
            return result
        result.merge(_extract_zip(input_dir, data, archive_name=display))
        return result

    if len(data) > MAX_IMAGE_BYTES:
        result.skipped.append(SkippedFile(display, _size_limit_reason(MAX_IMAGE_BYTES)))
        return result

    save_as = _resolve_image_basename(display, data, content_type)
    if save_as is None:
        if Path(rel.name).suffix.lower() in {".txt", ".md", ".json", ".xml", ".csv"}:
            result.skipped.append(SkippedFile(display, "не изображение"))
        else:
            result.skipped.append(
                SkippedFile(display, "допустимы только изображения или .zip")
            )
        return result

    if not _validate_image_bytes(data):
        result.skipped.append(
            SkippedFile(display, "файл не распознан как изображение")
        )
        return result

    dest = _unique_dest(input_dir, save_as)
    dest.write_bytes(data)
    result.saved.append(dest.name)
    return result


def _extract_zip(input_dir: Path, data: bytes, *, archive_name: str) -> IngestResult:
    result = IngestResult()
    if not _validate_zip_bytes(data):
        result.skipped.append(SkippedFile(archive_name, "повреждённый или не ZIP-архив"))
        return result

    input_resolved = input_dir.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if len(result.saved) >= MAX_PAGES_PER_UPLOAD:
                result.skipped.append(
                    SkippedFile(
                        archive_name,
                        f"лимит {MAX_PAGES_PER_UPLOAD} страниц за одну загрузку",
                    )
                )
                break
            inner = Path(info.filename.replace("\\", "/"))
            inner_display = f"{archive_name} → {inner.as_posix()}"
            if _is_junk_path(inner):
                result.skipped.append(SkippedFile(inner_display, "служебный файл в архиве"))
                continue
            if is_zip_filename(inner.name):
                result.skipped.append(
                    SkippedFile(inner_display, "вложенные архивы не поддерживаются")
                )
                continue
            if not is_page_filename(inner.name):
                result.skipped.append(
                    SkippedFile(
                        inner_display,
                        "в архиве берём только изображения (jpg, png, webp, gif)",
                    )
                )
                continue
            if info.file_size > MAX_IMAGE_BYTES:
                result.skipped.append(
                    SkippedFile(inner_display, _size_limit_reason(MAX_IMAGE_BYTES))
                )
                continue
            raw = zf.read(info)
            if len(raw) > MAX_IMAGE_BYTES:
                result.skipped.append(
                    SkippedFile(inner_display, _size_limit_reason(MAX_IMAGE_BYTES))
                )
                continue
            if not _validate_image_bytes(raw):
                result.skipped.append(
                    SkippedFile(inner_display, "не удалось прочитать как изображение")
                )
                continue
            dest = _unique_dest(input_dir, inner.name)
            try:
                dest.resolve().relative_to(input_resolved)
            except ValueError:
                result.skipped.append(SkippedFile(inner_display, "небезопасный путь в архиве"))
                continue
            dest.write_bytes(raw)
            result.saved.append(dest.name)

    if not result.saved and not any(s.name.startswith(archive_name) for s in result.skipped):
        result.skipped.append(
            SkippedFile(archive_name, "в архиве нет подходящих изображений")
        )
    return result
