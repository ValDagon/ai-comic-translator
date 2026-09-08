"""Фильтры логов uvicorn (меньше шума от HTMX polling)."""

from __future__ import annotations

import logging


class SuppressPathsAccessLogFilter(logging.Filter):
    """Не логировать частые GET (live poll, static)."""

    _SUBSTR = ("/ui/live", "/static/")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if '"GET ' not in msg:
            return True
        return not any(s in msg for s in self._SUBSTR)


def quiet_uvicorn_access_log() -> None:
    logging.getLogger("uvicorn.access").addFilter(SuppressPathsAccessLogFilter())
