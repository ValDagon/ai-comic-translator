"""
Оркестрация пайплайна: extract → parse → translate → render.

Используется из main.py и позже из FastAPI/worker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from extract_parse import Page, parse_all_pages
from render import RenderError, render_page
from run_extract import run_extraction
from translate_request import (
    DEFAULT_PAGES_PER_BATCH,
    DEFAULT_TARGET_LANG,
    MODEL,
    find_missing_translations,
    load_translations_file,
    translate_all,
    write_translations_file,
)

LogFn = Callable[[str], None]


class PipelineMode(str, Enum):
    EXTRACT = "extract"
    TRANSLATE = "translate"
    RENDER = "render"
    FULL = "full"


class PipelineError(Exception):
    """Ошибка пайплайна (данные, перевод, настройки)."""


@dataclass
class PipelineConfig:
    input_dir: Path
    out_dir: Path
    mode: PipelineMode = PipelineMode.FULL
    clean_dir: Path | None = None
    mit_repo: Path | None = None
    skip_extract: bool = False
    prep_manual: bool = False
    translations_in: Path | None = None
    translations_out: Path | None = None
    strict: bool = False
    target_lang: str = DEFAULT_TARGET_LANG
    font_path: str | None = None
    font_scale: float = 1.2
    model: str = MODEL
    pages_per_batch: int = DEFAULT_PAGES_PER_BATCH
    api_key: str | None = None
    project_root: Path | None = None


@dataclass
class PipelineResult:
    pages: list[Page] = field(default_factory=list)
    translations: dict[str, str] = field(default_factory=dict)
    rendered_paths: list[Path] = field(default_factory=list)
    skipped_clean: list[str] = field(default_factory=list)

    @property
    def regions_count(self) -> int:
        return sum(len(p.regions) for p in self.pages)


def resolve_font_path(font_path: str | None, base: Path) -> Path | None:
    if not font_path:
        return None
    path = Path(font_path).expanduser()
    if not path.is_absolute():
        path = base / path
    path = path.resolve()
    if not path.is_file():
        raise PipelineError(f"Шрифт не найден: {path}")
    return path


def _render_pages(
    config: PipelineConfig,
    result: PipelineResult,
    *,
    clean_dir: Path,
    root: Path,
    _log: LogFn,
) -> None:
    resolved_font = resolve_font_path(config.font_path, root)
    _log("Рендерю русский текст в очищенные страницы...")
    if resolved_font:
        _log(f"  шрифт: {resolved_font}")
    render_kwargs: dict = {"box_inflate": config.font_scale}
    if resolved_font:
        render_kwargs["font_path"] = str(resolved_font)

    config.out_dir.mkdir(parents=True, exist_ok=True)
    for page_idx, page in enumerate(result.pages):
        clean_path = clean_dir / page.source_path.name
        if not clean_path.exists():
            _log(f"[!] Нет очищенной версии {clean_path}, пропускаю")
            result.skipped_clean.append(page.source_path.name)
            continue
        out_path = config.out_dir / page.source_path.name
        try:
            render_page(
                page,
                clean_path,
                out_path,
                translations=result.translations,
                page_idx=page_idx,
                **render_kwargs,
            )
        except RenderError as exc:
            _log(f"[!] {exc} — пропускаю страницу")
            result.skipped_clean.append(page.source_path.name)
            continue
        result.rendered_paths.append(out_path)
        _log(f"  {out_path.name} готов")


def run_pipeline(
    config: PipelineConfig,
    *,
    log: LogFn | None = None,
) -> PipelineResult:
    _log = log or print
    root = config.project_root or Path(__file__).resolve().parent
    clean_dir = config.clean_dir or (config.out_dir / "_clean")
    result = PipelineResult()
    mode = config.mode

    do_extract = mode in (PipelineMode.EXTRACT, PipelineMode.FULL) and not config.skip_extract
    if do_extract:
        if config.mit_repo is None:
            raise PipelineError("Укажите mit_repo для извлечения")
        run_extraction(
            config.mit_repo,
            config.input_dir,
            clean_dir,
            prep_manual=config.prep_manual,
            log=_log,
        )
    elif mode == PipelineMode.FULL and config.skip_extract:
        _log(f"Пропускаю извлечение, использую уже готовые файлы в {clean_dir}")

    if mode == PipelineMode.EXTRACT:
        _log("Извлечение завершено.")
        return result

    _log("Парсю извлечённый текст...")
    result.pages = parse_all_pages(config.input_dir)
    _log(f"Найдено {len(result.pages)} страниц, {result.regions_count} облачков с текстом")
    if not result.pages:
        raise PipelineError(
            "Нет данных: в input_dir нет *_translations.txt (сначала extract)."
        )

    do_translate = mode in (PipelineMode.TRANSLATE, PipelineMode.FULL)
    if do_translate:
        if config.translations_in:
            if not config.translations_in.is_file():
                raise PipelineError(f"Файл переводов не найден: {config.translations_in}")
            result.translations = load_translations_file(config.translations_in)
            _log(f"Загружен перевод из {config.translations_in.resolve()}")
        else:
            _log("Отправляю на перевод...")
            _log(f"  модель: {config.model}")
            _log(f"  язык: {config.target_lang}")
            result.translations = translate_all(
                result.pages,
                api_key=config.api_key,
                model=config.model,
                pages_per_batch=config.pages_per_batch,
                log=_log,
                target_lang=config.target_lang,
                checkpoint_path=config.translations_out,
            )
            if config.translations_out:
                write_translations_file(config.translations_out, result.translations)
                _log(f"Сохранён кэш переводов: {config.translations_out}")

        missing = find_missing_translations(result.pages, result.translations)
        if missing:
            msg = (
                f"Нет перевода для {len(missing)} облачков "
                f"({', '.join(missing[:8])}{' …' if len(missing) > 8 else ''})"
            )
            if config.strict:
                raise PipelineError(msg)
            _log(f"[!] {msg}")

    if mode == PipelineMode.TRANSLATE:
        _log("Перевод завершён.")
        return result

    do_render = mode in (PipelineMode.RENDER, PipelineMode.FULL)
    if do_render:
        if not result.translations:
            src = config.translations_in or config.translations_out
            if src and src.is_file():
                result.translations = load_translations_file(src)
            else:
                raise PipelineError("Нет translations.json — сначала translate.")
        _render_pages(config, result, clean_dir=clean_dir, root=root, _log=_log)

    _log(f"Готово. Результат в {config.out_dir}")
    return result
