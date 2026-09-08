"""Короткий лог job'а для веб-UI (без технического шума)."""

from __future__ import annotations

import re

DEFAULT_LOCALE = "en"

_KIND_KEYS = {
    "extract": "job.kind.extract",
    "translate": "job.kind.translate",
    "render": "job.kind.render",
    "full": "job.kind.full",
}

_STATUS_KEYS = {
    "queued": "job.status.queued",
    "running": "job.status.running",
    "done": "job.status.done",
    "failed": "job.status.failed",
}

_SKIP_SUBSTR = (
    "Запускаю:",
    "python -m manga_translator",
    "manga_translator",
    "--config-file",
    "/Users/",
    "venv/",
    "site-packages",
)

_SKIP_RE = re.compile(
    r"^(INFO|DEBUG|WARNING|ERROR):|"
    r"^\[\d+/\d+\]|"
    r"^\s*\d+%|"
    r"Downloading|"
    r"Loading model",
    re.I,
)

# Already-localized lines stored in SQLite (legacy RU) → keys
_STORED_TO_KEY = {
    "Запуск…": "log.start",
    "Использую уже очищенные страницы.": "log.skip_extract",
    "Считываю текст с страниц…": "log.parse",
    "Отправляю текст на перевод…": "log.send_translate",
    "Перевод загружен из файла.": "log.loaded_translations",
    "Вставляю русский текст на страницы…": "log.rendering",
    "Вставляю перевод на страницы…": "log.rendering",
    "Перевод сохранён.": "log.saved_cache",
    "Извлечение завершено.": "log.extract_done",
    "Перевод завершён.": "log.translate_done",
    "Всё готово.": "log.all_done",
    "Ожидает worker…": "log.idle_queued",
    "Завершено успешно.": "log.idle_done",
    "Не удалось выполнить.": "log.idle_failed",
    "Извлечение текста и очистка облачков (может занять несколько минут)…": "log.extract_hint",
}


def _t(key: str, locale: str = DEFAULT_LOCALE, **kwargs) -> str:
    from web.i18n import t

    return t(key, locale=locale, **kwargs)


def kind_label(kind: str, *, locale: str = DEFAULT_LOCALE) -> str:
    key = _KIND_KEYS.get(kind)
    return _t(key, locale=locale) if key else kind


def status_label(status: str, *, locale: str = DEFAULT_LOCALE) -> str:
    key = _STATUS_KEYS.get(status)
    return _t(key, locale=locale) if key else status


def user_facing_log(
    raw: str,
    *,
    status: str,
    kind: str,
    error: str | None = None,
    max_lines: int = 10,
    locale: str = DEFAULT_LOCALE,
) -> str:
    lines_out: list[str] = []
    seen_extract_hint = False

    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if any(x in s for x in _SKIP_SUBSTR):
            if not seen_extract_hint and ("manga_translator" in s or "extract может" in s):
                lines_out.append(_t("log.extract_hint", locale=locale))
                seen_extract_hint = True
            continue
        if _SKIP_RE.search(s):
            continue
        if len(s) > 160 and "/" in s:
            continue

        friendly = _map_line(s, locale=locale)
        if friendly:
            lines_out.append(friendly)

    if not lines_out:
        return _idle_message(status, kind, error, locale=locale)

    # убрать подряд идущие дубликаты
    compact: list[str] = []
    for ln in lines_out:
        if compact and compact[-1] == ln:
            continue
        compact.append(ln)

    if len(compact) > max_lines:
        hidden = len(compact) - max_lines + 1
        compact = (
            compact[:1]
            + [_t("log.more_steps", locale=locale, n=hidden)]
            + compact[-(max_lines - 2) :]
        )

    return "\n".join(compact)


def _map_line(s: str, *, locale: str) -> str | None:
    stored_key = _STORED_TO_KEY.get(s)
    if stored_key:
        return _t(stored_key, locale=locale)
    if s.startswith("Готова страница:"):
        name = s.split("Готова страница:", 1)[1].strip()
        return _t("log.page_ready", locale=locale, name=name)
    if s.startswith("Page ready:"):
        name = s.split("Page ready:", 1)[1].strip()
        return _t("log.page_ready", locale=locale, name=name)

    if s.startswith("Старт job"):
        return _t("log.start", locale=locale)
    if "Пропускаю извлечение" in s:
        return _t("log.skip_extract", locale=locale)
    if s.startswith("Парсю"):
        return _t("log.parse", locale=locale)
    if s.startswith("Найдено") and "страниц" in s:
        return s.replace("облачков с текстом", "реплик")
    if s.startswith("Отправляю на перевод"):
        return _t("log.send_translate", locale=locale)
    if s.startswith("  модель:"):
        return None
    if s.startswith("  перевод:") or s.startswith("  пачка"):
        return s.strip()
    if "Загружен перевод" in s:
        return _t("log.loaded_translations", locale=locale)
    if s.startswith("Рендерю"):
        return _t("log.rendering", locale=locale)
    if s.endswith("готов") and not s.startswith("Готово"):
        name = s.replace("  ", "").split(" готов")[0].strip()
        return _t("log.page_ready", locale=locale, name=name)
    if s.startswith("Сохранён кэш"):
        return _t("log.saved_cache", locale=locale)
    if s.startswith("Извлечение завершено"):
        return _t("log.extract_done", locale=locale)
    if s.startswith("Перевод завершён"):
        return _t("log.translate_done", locale=locale)
    if s.startswith("Готово."):
        return _t("log.all_done", locale=locale)
    if s.startswith("[!]"):
        return s.replace("[!] ", "⚠ ")
    if "extract может занять" in s.lower():
        return _t("log.extract_hint", locale=locale)
    if "Готово. Очищенные" in s or "translations.txt" in s:
        return _t("log.extract_done", locale=locale)
    return None


def line_for_job_db(raw_line: str) -> str | None:
    """В SQLite/UI — только короткие строки; полный поток — в консоли worker."""
    s = raw_line.strip()
    if not s:
        return None
    mapped = _map_line(s, locale="ru")
    if mapped:
        return mapped
    if "Нет перевода" in s or "Укажите mit_repo" in s or "manga-image-translator" in s:
        return s[:200]
    return None


def _idle_message(
    status: str, kind: str, error: str | None, *, locale: str
) -> str:
    if status == "queued":
        return _t("log.idle_queued", locale=locale)
    if status == "running":
        if kind in ("extract", "full"):
            return _t("log.idle_extract", locale=locale)
        if kind == "translate":
            return _t("log.idle_translate", locale=locale)
        if kind == "render":
            return _t("log.idle_render", locale=locale)
        return _t("log.idle_running", locale=locale)
    if status == "done":
        return _t("log.idle_done", locale=locale)
    if status == "failed":
        return error or _t("log.idle_failed", locale=locale)
    return _t("log.dash", locale=locale)
