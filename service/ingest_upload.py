"""Общая обработка загрузки страниц в input/."""

from __future__ import annotations

from pathlib import Path

from fastapi import UploadFile

from service.upload_pages import (
    MAX_IMAGE_BYTES,
    MAX_UPLOAD_BYTES,
    IngestResult,
    SkippedFile,
    is_zip_filename,
    process_upload,
)

UploadPart = tuple[str, bytes, str | None]

_READ_CHUNK = 64 * 1024


class UploadTooLarge(Exception):
    """Файл превысил MAX_UPLOAD_BYTES при потоковом чтении."""


def read_upload_capped(file_obj, *, max_bytes: int = MAX_UPLOAD_BYTES) -> bytes:
    """Читает upload с жёстким лимитом (не буферизует сверх max_bytes)."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file_obj.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise UploadTooLarge(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def ingest_upload_parts(input_dir: Path, parts: list[UploadPart]) -> IngestResult:
    input_dir.mkdir(parents=True, exist_ok=True)
    total = IngestResult()
    for raw_name, data, content_type in parts:
        name = raw_name.strip()
        if not name:
            continue
        total.merge(
            process_upload(
                input_dir,
                filename=name,
                data=data,
                content_type=content_type,
            )
        )
    return total


def ingest_upload_files(input_dir: Path, files: list[UploadFile]) -> IngestResult:
    parts: list[UploadPart] = []
    skipped: list[SkippedFile] = []
    for uf in files:
        raw_name = (uf.filename or "").strip()
        if not raw_name:
            continue
        limit = MAX_UPLOAD_BYTES if is_zip_filename(raw_name) else MAX_IMAGE_BYTES
        try:
            data = read_upload_capped(uf.file, max_bytes=limit)
        except UploadTooLarge:
            skipped.append(
                SkippedFile(
                    raw_name,
                    f"файл больше лимита {limit // (1024 * 1024)} MiB",
                )
            )
            continue
        except Exception as exc:
            skipped.append(SkippedFile(raw_name, f"не удалось прочитать файл ({exc})"))
            continue
        parts.append((raw_name, data, uf.content_type))
    result = ingest_upload_parts(input_dir, parts)
    result.skipped.extend(skipped)
    return result
