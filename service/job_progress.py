"""Оценка общего прогресса job для UI (шаг 5%)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from service.db import JobRow
from service.pipeline_job_options import JobOptions

PROGRESS_STEP = 5

_BATCH_RE = re.compile(r"пачка\s+(\d+)\s*/\s*(\d+)", re.I)
_PAGE_READY_RE = re.compile(r"(?:Готова страница:|Page ready:)", re.I)


@dataclass(frozen=True)
class JobProgress:
    percent: int
    state: str  # idle | queued | running | done | failed
    page_current: int | None = None
    page_total: int | None = None


def snap_percent(value: float) -> int:
    """Округление до ближайших 5%, в диапазоне 0–100."""
    if value <= 0:
        return 0
    if value >= 100:
        return 100
    return int(round(value / PROGRESS_STEP) * PROGRESS_STEP)


def pick_progress_job(jobs: Sequence[JobRow]) -> JobRow | None:
    """Активный job, иначе самый свежий."""
    for job in jobs:
        if job.status in ("queued", "running"):
            return job
    return jobs[0] if jobs else None


def page_progress(
    job: JobRow | None,
    *,
    input_count: int,
    out_count: int,
) -> tuple[int | None, int | None]:
    """Current page index and total pages when render progress is known."""
    if input_count <= 0:
        return None, None
    if job is None:
        if out_count > 0:
            return min(out_count, input_count), input_count
        return None, None
    if job.status == "done":
        return input_count, input_count
    log = job.log or ""
    ready = _pages_ready_in_log(log)
    in_render = job.kind == "render" or _has(
        log, "Вставляю", "Рендерю", "render", "Placing"
    )
    if in_render or ready > 0 or out_count > 0:
        current = max(out_count, ready)
        if current > 0 or job.status in ("queued", "running"):
            return min(max(current, 1 if job.status == "running" else 0), input_count), input_count
    return None, None


def estimate_job_progress(
    job: JobRow,
    *,
    input_count: int = 0,
    out_count: int = 0,
) -> JobProgress:
    page_current, page_total = page_progress(
        job, input_count=input_count, out_count=out_count
    )
    if job.status == "queued":
        return JobProgress(0, "queued", page_current, page_total)
    if job.status == "done":
        return JobProgress(100, "done", page_current, page_total)

    opts = JobOptions.from_json(job.options_json)
    skip_extract = job.kind in ("translate", "render") or (
        job.kind == "full" and opts.skip_extract
    )
    raw = estimate_running_percent(
        kind=job.kind,
        log=job.log or "",
        skip_extract=skip_extract,
        use_existing_translations=opts.use_existing_translations,
        input_count=input_count,
        out_count=out_count,
    )
    percent = snap_percent(raw)
    if job.status == "failed":
        return JobProgress(percent, "failed", page_current, page_total)
    return JobProgress(percent, "running", page_current, page_total)


def estimate_running_percent(
    *,
    kind: str,
    log: str,
    skip_extract: bool,
    use_existing_translations: bool,
    input_count: int,
    out_count: int,
) -> float:
    """Грубая оценка 0–99 по логу и счётчикам страниц."""
    if kind == "extract":
        return _extract_only_percent(log)
    if kind == "translate":
        return _translate_span_percent(log, use_existing_translations, lo=5, hi=95)
    if kind == "render":
        return _render_span_percent(log, input_count, out_count, lo=5, hi=95)
    # full
    if skip_extract:
        return _full_skip_extract_percent(
            log, use_existing_translations, input_count, out_count
        )
    return _full_with_extract_percent(
        log, use_existing_translations, input_count, out_count
    )


def project_progress(
    jobs: Sequence[JobRow],
    *,
    input_count: int = 0,
    out_count: int = 0,
) -> JobProgress:
    job = pick_progress_job(jobs)
    if job is None:
        page_current, page_total = page_progress(
            None, input_count=input_count, out_count=out_count
        )
        if input_count > 0 and out_count >= input_count:
            return JobProgress(100, "done", page_current, page_total)
        return JobProgress(0, "idle", page_current, page_total)
    return estimate_job_progress(
        job, input_count=input_count, out_count=out_count
    )


def _has(log: str, *needles: str) -> bool:
    return any(n in log for n in needles)


def _last_batch_fraction(log: str) -> float | None:
    last: re.Match[str] | None = None
    for match in _BATCH_RE.finditer(log):
        last = match
    if last is None:
        return None
    current = int(last.group(1))
    total = int(last.group(2))
    if total <= 0:
        return None
    # текущая пачка в работе — считаем половину шага
    return min(1.0, max(0.0, (current - 0.5) / total))


def _pages_ready_in_log(log: str) -> int:
    return len(_PAGE_READY_RE.findall(log))


def _render_fraction(log: str, input_count: int, out_count: int) -> float:
    ready = _pages_ready_in_log(log)
    if input_count > 0:
        return min(1.0, max(ready, 0) / input_count)
    if out_count > 0 and ready == 0:
        # лог ещё не накопил строки, но файлы уже есть
        return 0.5
    if ready > 0:
        return min(1.0, 0.15 * ready)
    return 0.0


def _extract_only_percent(log: str) -> float:
    if _has(log, "Извлечение завершено", "Extract done"):
        return 95
    if _has(log, "Извлечение текста", "extract может", "Идёт извлечение"):
        return 35
    if _has(log, "Запуск", "Старт"):
        return 10
    return 5


def _translate_span_percent(
    log: str, use_existing: bool, *, lo: float, hi: float
) -> float:
    span = hi - lo
    if _has(log, "Перевод завершён", "Translation done"):
        return hi
    if use_existing or _has(log, "Перевод загружен", "loaded from"):
        return lo + span * 0.85
    batch = _last_batch_fraction(log)
    if batch is not None:
        return lo + span * (0.15 + 0.75 * batch)
    if _has(log, "Отправляю", "перевод", "translate"):
        return lo + span * 0.2
    if _has(log, "Считываю", "Найдено", "Парсю"):
        return lo + span * 0.1
    return lo


def _render_span_percent(
    log: str, input_count: int, out_count: int, *, lo: float, hi: float
) -> float:
    span = hi - lo
    if _has(log, "Всё готово", "Готово.", "All done"):
        return hi
    frac = _render_fraction(log, input_count, out_count)
    if frac > 0 or _has(log, "Вставляю", "Рендерю", "render"):
        return lo + span * max(0.08, frac)
    return lo


def _full_with_extract_percent(
    log: str,
    use_existing: bool,
    input_count: int,
    out_count: int,
) -> float:
    # extract 5–35 · parse 40 · translate 45–70 · render 75–95
    if _has(log, "Всё готово", "Готово."):
        return 98
    if _has(log, "Вставляю", "Рендерю") or _pages_ready_in_log(log) > 0:
        return _render_span_percent(log, input_count, out_count, lo=75, hi=95)
    if _has(log, "Перевод завершён", "Перевод сохранён", "Перевод загружен"):
        return 72
    if (
        _last_batch_fraction(log) is not None
        or _has(log, "Отправляю текст на перевод", "Отправляю на перевод")
    ):
        return _translate_span_percent(log, use_existing, lo=45, hi=70)
    if _has(log, "Считываю", "Найдено", "Парсю", "Извлечение завершено"):
        return 40
    if _has(log, "Извлечение текста", "extract может", "Использую уже очищенные"):
        return 25
    if _has(log, "Запуск", "Старт"):
        return 10
    return 5


def _full_skip_extract_percent(
    log: str,
    use_existing: bool,
    input_count: int,
    out_count: int,
) -> float:
    # parse 5–15 · translate 20–65 · render 70–95
    if _has(log, "Всё готово", "Готово."):
        return 98
    if _has(log, "Вставляю", "Рендерю") or _pages_ready_in_log(log) > 0:
        return _render_span_percent(log, input_count, out_count, lo=70, hi=95)
    if _has(log, "Перевод завершён", "Перевод сохранён", "Перевод загружен"):
        return 68
    if (
        _last_batch_fraction(log) is not None
        or _has(log, "Отправляю текст на перевод", "Отправляю на перевод")
    ):
        return _translate_span_percent(log, use_existing, lo=20, hi=65)
    if _has(log, "Считываю", "Найдено", "Парсю", "Использую уже очищенные"):
        return 15
    if _has(log, "Запуск", "Старт"):
        return 5
    return 5
