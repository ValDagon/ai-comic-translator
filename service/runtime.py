"""Пути и настройки для API/worker (config.toml + env)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from config_loader import DEFAULT_CONFIG_PATH, load_settings
from translate_request import DEFAULT_PAGES_PER_BATCH, DEFAULT_TARGET_LANG, MODEL, target_language


APP_ENV_DEV = "dev"
APP_ENV_RELEASE = "release"
INSECURE_DEV_SESSION_SECRET = "dev-insecure-change-me"
MIN_RELEASE_SESSION_SECRET_LEN = 32


class RuntimeSecurityError(RuntimeError):
    """Небезопасная конфигурация для текущего режима — отказ старта."""


@dataclass(frozen=True)
class RuntimeSettings:
    repo_root: Path
    config_path: Path
    data_dir: Path
    database_path: Path
    mit_repo: Path | None
    api_key: str | None
    model: str
    pages_per_batch: int
    target_lang: str
    font_path: str | None
    font_scale: float
    host: str
    port: int
    worker_poll_sec: float
    ui_developer_mode: bool
    session_secret: str
    session_https_only: bool
    allow_register: bool
    app_env: str
    public_base_url: str
    google_client_id: str
    google_client_secret: str
    apple_client_id: str
    apple_team_id: str
    apple_key_id: str
    apple_private_key: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_use_tls: bool

    @property
    def is_dev(self) -> bool:
        return self.app_env == APP_ENV_DEV


def resolve_runtime(
    *,
    repo_root: Path | None = None,
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> RuntimeSettings:
    root = (repo_root or Path(__file__).resolve().parent.parent).resolve()
    cfg_path = config_path if config_path.is_absolute() else root / config_path
    cfg = load_settings(cfg_path)

    data_dir = Path(
        os.environ.get("COMIC_DATA_DIR")
        or cfg.server.data_dir
        or "data"
    )
    if not data_dir.is_absolute():
        data_dir = root / data_dir

    db_path = Path(
        os.environ.get("COMIC_DATABASE")
        or cfg.server.database
        or str(data_dir / "app.db")
    )
    if not db_path.is_absolute():
        db_path = root / db_path

    mit_raw = os.environ.get("MIT_REPO") or cfg.server.mit_repo
    mit_repo = Path(mit_raw).expanduser() if mit_raw else None
    if mit_repo and not mit_repo.is_absolute():
        mit_repo = root / mit_repo
    if mit_repo and not mit_repo.is_dir():
        mit_repo = None

    api_key = cfg.openrouter.api_key or os.environ.get("OPENROUTER_API_KEY")
    model = cfg.openrouter.model or MODEL
    ppb = (
        cfg.openrouter.pages_per_batch
        if cfg.openrouter.pages_per_batch is not None
        else DEFAULT_PAGES_PER_BATCH
    )
    try:
        target_lang = target_language(
            cfg.openrouter.target_lang or DEFAULT_TARGET_LANG
        ).code
    except ValueError:
        target_lang = DEFAULT_TARGET_LANG
    font_scale = (
        cfg.render.font_scale if cfg.render.font_scale is not None else 1.2
    )

    host = os.environ.get("COMIC_API_HOST") or cfg.server.host or "127.0.0.1"
    port = int(os.environ.get("COMIC_API_PORT") or cfg.server.port or 8000)
    poll = float(os.environ.get("COMIC_WORKER_POLL_SEC") or "2.0")

    session_secret = (
        os.environ.get("COMIC_SESSION_SECRET")
        or os.environ.get("SESSION_SECRET")
        or INSECURE_DEV_SESSION_SECRET
    )
    session_https_only = _env_flag("COMIC_SESSION_HTTPS_ONLY")
    allow_raw = (os.environ.get("COMIC_ALLOW_REGISTER") or "1").strip().lower()
    allow_register = allow_raw not in ("0", "false", "no", "off")

    # Без env — dev (удобно локально). На сервере Compose задаёт release явно.
    app_env_raw = (os.environ.get("COMIC_APP_ENV") or APP_ENV_DEV).strip().lower()
    if app_env_raw in ("release", "prod", "production"):
        app_env = APP_ENV_RELEASE
    else:
        app_env = APP_ENV_DEV
    # Env wins: local always developer UI; release never.
    ui_developer_mode = app_env == APP_ENV_DEV

    public_base_url = (
        os.environ.get("COMIC_PUBLIC_BASE_URL")
        or (f"https://{os.environ['DOMAIN']}" if os.environ.get("DOMAIN") else "")
        or f"http://{host}:{port}"
    ).rstrip("/")

    apple_key = (os.environ.get("COMIC_APPLE_PRIVATE_KEY") or "").replace(
        "\\n", "\n"
    )
    smtp_tls_raw = (os.environ.get("COMIC_SMTP_TLS") or "1").strip().lower()
    smtp_use_tls = smtp_tls_raw not in ("0", "false", "no", "off")

    settings = RuntimeSettings(
        repo_root=root,
        config_path=cfg_path,
        data_dir=data_dir.resolve(),
        database_path=db_path.resolve(),
        mit_repo=mit_repo.resolve() if mit_repo else None,
        api_key=api_key,
        model=model,
        pages_per_batch=ppb,
        target_lang=target_lang,
        font_path=cfg.render.font_path,
        font_scale=font_scale,
        host=host,
        port=port,
        worker_poll_sec=poll,
        ui_developer_mode=ui_developer_mode,
        session_secret=session_secret,
        session_https_only=session_https_only,
        allow_register=allow_register,
        app_env=app_env,
        public_base_url=public_base_url,
        google_client_id=(os.environ.get("COMIC_GOOGLE_CLIENT_ID") or "").strip(),
        google_client_secret=(
            os.environ.get("COMIC_GOOGLE_CLIENT_SECRET") or ""
        ).strip(),
        apple_client_id=(os.environ.get("COMIC_APPLE_CLIENT_ID") or "").strip(),
        apple_team_id=(os.environ.get("COMIC_APPLE_TEAM_ID") or "").strip(),
        apple_key_id=(os.environ.get("COMIC_APPLE_KEY_ID") or "").strip(),
        apple_private_key=apple_key.strip(),
        smtp_host=(os.environ.get("COMIC_SMTP_HOST") or "").strip(),
        smtp_port=int(os.environ.get("COMIC_SMTP_PORT") or "587"),
        smtp_username=(os.environ.get("COMIC_SMTP_USER") or "").strip(),
        smtp_password=(os.environ.get("COMIC_SMTP_PASSWORD") or "").strip(),
        smtp_from=(os.environ.get("COMIC_SMTP_FROM") or "").strip(),
        smtp_use_tls=smtp_use_tls,
    )
    return settings


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _is_loopback_host(host: str) -> bool:
    h = (host or "").strip().lower()
    return h in ("127.0.0.1", "localhost", "::1")


def assert_runtime_secure(settings: RuntimeSettings) -> None:
    """Отказ старта при небезопасных дефолтах в release / публичном bind."""
    secret = settings.session_secret or ""
    weak_secret = (
        not secret
        or secret == INSECURE_DEV_SESSION_SECRET
        or len(secret) < MIN_RELEASE_SESSION_SECRET_LEN
    )

    if settings.app_env == APP_ENV_RELEASE:
        if weak_secret:
            raise RuntimeSecurityError(
                "COMIC_APP_ENV=release требует COMIC_SESSION_SECRET "
                f"(≥{MIN_RELEASE_SESSION_SECRET_LEN} символов, не дефолт). "
                "Сгенерируйте: python3 -c \"import secrets; print(secrets.token_urlsafe(48))\""
            )
        if not settings.session_https_only:
            raise RuntimeSecurityError(
                "COMIC_APP_ENV=release требует COMIC_SESSION_HTTPS_ONLY=1 "
                "(иначе сессионная cookie уходит без флага Secure). "
                "Задайте COMIC_SESSION_HTTPS_ONLY=1, когда сервис уже за HTTPS."
            )
        return

    # dev: авто-логин допустим только на loopback, иначе нужен явный флаг.
    if not _is_loopback_host(settings.host) and not _env_flag(
        "COMIC_ALLOW_INSECURE_DEV"
    ):
        raise RuntimeSecurityError(
            "COMIC_APP_ENV=dev с авто-логином нельзя на host="
            f"{settings.host!r}. Задайте COMIC_APP_ENV=release и секрет, "
            "или COMIC_ALLOW_INSECURE_DEV=1 для явного разрешения."
        )
