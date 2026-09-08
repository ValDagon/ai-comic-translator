"""Загрузка config.toml (+ опциональный config.local.toml для секретов)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path("config.toml")
DEFAULT_LOCAL_CONFIG_PATH = Path("config.local.toml")


@dataclass
class OpenRouterSettings:
    api_key: str | None = None
    model: str | None = None
    pages_per_batch: int | None = None
    target_lang: str | None = None


@dataclass
class RenderSettings:
    font_path: str | None = None
    font_scale: float | None = None


@dataclass
class ServerSettings:
    data_dir: str | None = None
    database: str | None = None
    mit_repo: str | None = None
    host: str | None = None
    port: int | None = None


@dataclass
class UiSettings:
    # RELEASE: leave false — otherwise the UI shows a developer-mode toggle
    developer_mode: bool = False


@dataclass
class AppSettings:
    openrouter: OpenRouterSettings
    render: RenderSettings
    server: ServerSettings
    ui: UiSettings


def load_settings(
    path: Path = DEFAULT_CONFIG_PATH,
    local_path: Path | None = None,
) -> AppSettings:
    """Читает публичный config и накладывает локальный оверлей (секреты).

    По умолчанию рядом с ``config.toml`` ищется ``config.local.toml``
    (в .gitignore). Ключи из local перекрывают базовые.
    """
    raw: dict[str, Any] = {}
    if path.is_file():
        raw = tomllib.loads(path.read_text(encoding="utf-8"))

    overlay_path = local_path
    if overlay_path is None and path.name == "config.toml":
        overlay_path = path.with_name(DEFAULT_LOCAL_CONFIG_PATH.name)
    if overlay_path is not None and overlay_path.is_file():
        local_raw = tomllib.loads(overlay_path.read_text(encoding="utf-8"))
        raw = _deep_merge(raw, local_raw)

    return _settings_from_raw(raw)


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _settings_from_raw(raw: dict[str, Any]) -> AppSettings:
    o = raw.get("openrouter") or {}
    r = raw.get("render") or {}
    s = raw.get("server") or {}
    u = raw.get("ui") or {}

    return AppSettings(
        openrouter=OpenRouterSettings(
            api_key=_opt_str(o.get("api_key")),
            model=_opt_str(o.get("model")),
            pages_per_batch=_opt_int(o.get("pages_per_batch")),
            target_lang=_opt_str(o.get("target_lang")),
        ),
        render=RenderSettings(
            font_path=_opt_str(r.get("font_path")),
            font_scale=_opt_float(r.get("font_scale")),
        ),
        server=ServerSettings(
            data_dir=_opt_str(s.get("data_dir")),
            database=_opt_str(s.get("database")),
            mit_repo=_opt_str(s.get("mit_repo")),
            host=_opt_str(s.get("host")),
            port=_opt_int(s.get("port")),
        ),
        ui=UiSettings(
            developer_mode=_opt_bool(u.get("developer_mode")),
        ),
    )


def _opt_str(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _opt_int(value) -> int | None:
    if value is None:
        return None
    return int(value)


def _opt_float(value) -> float | None:
    if value is None:
        return None
    return float(value)


def _opt_bool(value) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
