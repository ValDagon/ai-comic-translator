"""Опции job — те же параметры, что у CLI main.py, с валидацией."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from service.paths import ProjectPaths
from service.runtime import RuntimeSettings
from translate_request import DEFAULT_TARGET_LANG, MODEL, target_language

JobKind = str  # extract | translate | render | full

# Client-supplied models outside this set are ignored in release.
ALLOWED_MODELS = frozenset(
    {
        MODEL,
        "x-ai/grok-4.3",
        "x-ai/grok-4",
        "openai/gpt-4.1-mini",
        "openai/gpt-4o-mini",
        "google/gemini-2.5-flash",
        "anthropic/claude-sonnet-4",
    }
)

PRIVILEGED_OPTION_KEYS = frozenset({"mit_repo", "font_path", "model"})


@dataclass
class TargetLanguageChoice:
    code: str
    label: str


def target_language_choices() -> list[TargetLanguageChoice]:
    from translate_request import SUPPORTED_TARGET_LANGUAGES

    return [
        TargetLanguageChoice(code=lang.code, label=lang.label)
        for lang in SUPPORTED_TARGET_LANGUAGES
    ]


@dataclass
class JobOptions:
    """Переопределения относительно config.toml / RuntimeSettings."""

    skip_extract: bool = False
    prep_manual: bool = False
    strict: bool = False
    use_existing_translations: bool = False
    save_translations: bool = True
    target_lang: str = DEFAULT_TARGET_LANG
    font_path: str | None = None
    font_scale: float | None = None
    model: str | None = None
    pages_per_batch: int | None = None
    mit_repo: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str | None) -> JobOptions:
        if not raw or not raw.strip():
            return cls()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return cls()
        if not isinstance(data, dict):
            return cls()
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> JobOptions:
        def _bool(key: str, default: bool) -> bool:
            v = data.get(key, default)
            return bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "on", "yes")

        def _opt_str(key: str) -> str | None:
            v = data.get(key)
            if v is None:
                return None
            s = str(v).strip()
            return s or None

        def _opt_float(key: str) -> float | None:
            v = data.get(key)
            if v is None or v == "":
                return None
            return float(v)

        def _opt_int(key: str) -> int | None:
            v = data.get(key)
            if v is None or v == "":
                return None
            return int(v)

        lang = str(data.get("target_lang") or DEFAULT_TARGET_LANG).strip().lower()
        try:
            target_language(lang)
        except ValueError:
            lang = DEFAULT_TARGET_LANG

        return cls(
            skip_extract=_bool("skip_extract", False),
            prep_manual=_bool("prep_manual", False),
            strict=_bool("strict", False),
            use_existing_translations=_bool("use_existing_translations", False),
            save_translations=_bool("save_translations", True),
            target_lang=lang,
            font_path=_opt_str("font_path"),
            font_scale=_opt_float("font_scale"),
            model=_opt_str("model"),
            pages_per_batch=_opt_int("pages_per_batch"),
            mit_repo=_opt_str("mit_repo"),
        )


@dataclass
class ResolvedJobOptions:
    skip_extract: bool
    prep_manual: bool
    strict: bool
    use_existing_translations: bool
    save_translations: bool
    target_lang: str
    font_path: str | None
    font_scale: float
    model: str
    pages_per_batch: int
    mit_repo: str | None


def sanitize_client_job_options(
    opts: JobOptions,
    settings: RuntimeSettings,
) -> JobOptions:
    """В release убираем клиентские override путей/модели; в dev — allowlist модели."""
    if settings.is_dev and settings.ui_developer_mode:
        if opts.model and opts.model not in ALLOWED_MODELS:
            # Dev may still experiment, but unknown models stay as-is for CLI parity.
            pass
        return opts

    return JobOptions(
        skip_extract=opts.skip_extract,
        prep_manual=opts.prep_manual,
        strict=opts.strict,
        use_existing_translations=opts.use_existing_translations,
        save_translations=opts.save_translations,
        target_lang=opts.target_lang,
        font_path=None,
        font_scale=opts.font_scale,
        model=None if not opts.model or opts.model not in ALLOWED_MODELS else opts.model,
        pages_per_batch=opts.pages_per_batch,
        mit_repo=None,
    )


def font_path_allowed(
    font_path: str | None,
    *,
    repo_root: Path,
    data_dir: Path,
    project_id: str | None = None,
) -> bool:
    """True если путь пуст или лежит под repo / project fonts / data_dir."""
    if not font_path:
        return True
    path = Path(font_path).expanduser()
    if not path.is_absolute():
        path = (repo_root / path).resolve()
    else:
        path = path.resolve()
    allowed_roots = [
        repo_root.resolve(),
        data_dir.resolve(),
        (repo_root / "fonts").resolve(),
    ]
    if project_id:
        allowed_roots.append(
            (data_dir / "projects" / project_id / "fonts").resolve()
        )
    for root in allowed_roots:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            continue
    # System fonts used in tests / defaults (DejaVu, Arial).
    from render import DEFAULT_FONT_CANDIDATES

    return str(path) in {str(Path(p).resolve()) for p in DEFAULT_FONT_CANDIDATES if Path(p).is_file()}


def validate_job_options(
    kind: JobKind,
    opts: JobOptions,
    *,
    locale: str = "en",
) -> str | None:
    """Возвращает локализованный текст ошибки или None."""
    from web.i18n import t

    if kind == "extract":
        if opts.skip_extract:
            return t("err.skip_extract_only", locale=locale)
        if opts.use_existing_translations:
            return t("err.existing_on_extract", locale=locale)
    if opts.skip_extract and opts.prep_manual:
        return t("err.prep_vs_skip", locale=locale)
    if kind in ("translate", "full") and opts.use_existing_translations and not opts.save_translations:
        return t("err.use_existing_vs_save", locale=locale)
    if opts.font_scale is not None and opts.font_scale <= 0:
        return t("err.font_scale", locale=locale)
    if opts.pages_per_batch is not None and opts.pages_per_batch < 0:
        return t("err.pages_per_batch", locale=locale)
    try:
        target_language(opts.target_lang)
    except ValueError as e:
        return str(e)
    return None


def merge_with_runtime(settings: RuntimeSettings, opts: JobOptions) -> ResolvedJobOptions:
    model = opts.model or settings.model
    if not settings.is_dev and opts.model and opts.model not in ALLOWED_MODELS:
        model = settings.model
    return ResolvedJobOptions(
        skip_extract=opts.skip_extract,
        prep_manual=opts.prep_manual,
        strict=opts.strict,
        use_existing_translations=opts.use_existing_translations,
        save_translations=opts.save_translations,
        target_lang=opts.target_lang,
        font_path=opts.font_path or settings.font_path,
        font_scale=(
            opts.font_scale if opts.font_scale is not None else settings.font_scale
        ),
        model=model,
        pages_per_batch=(
            opts.pages_per_batch
            if opts.pages_per_batch is not None
            else settings.pages_per_batch
        ),
        mit_repo=opts.mit_repo or (str(settings.mit_repo) if settings.mit_repo else None),
    )


def job_options_from_form(form: dict[str, Any]) -> JobOptions:
    def _form_bool(name: str, *, default: bool) -> bool:
        if name not in form:
            return default
        v = form.get(name)
        if isinstance(v, list):
            v = v[-1]
        return str(v).lower() in ("1", "true", "on", "yes")

    mapping = {
        "skip_extract": _form_bool("skip_extract", default=False),
        "prep_manual": _form_bool("prep_manual", default=False),
        "strict": _form_bool("strict", default=False),
        "use_existing_translations": _form_bool("use_existing_translations", default=False),
        "save_translations": _form_bool("save_translations", default=True),
        "target_lang": form.get("target_lang"),
        "font_path": form.get("font_path"),
        "font_scale": form.get("font_scale"),
        "model": form.get("model"),
        "pages_per_batch": form.get("pages_per_batch"),
        "mit_repo": form.get("mit_repo"),
    }
    return JobOptions.from_mapping(mapping)


def defaults_for_form(
    settings: RuntimeSettings,
    *,
    repo_root: Path | None = None,
    paths: ProjectPaths | None = None,
) -> dict[str, Any]:
    font_path = settings.font_path or ""
    if repo_root is not None and paths is not None:
        from service.project_font import project_font_path_for_repo

        project_font = project_font_path_for_repo(repo_root, paths)
        if project_font:
            font_path = project_font
    return {
        "font_path": font_path,
        "font_scale": settings.font_scale,
        "model": settings.model,
        "pages_per_batch": settings.pages_per_batch,
        "mit_repo": str(settings.mit_repo) if settings.mit_repo else "",
        "target_lang": settings.target_lang or DEFAULT_TARGET_LANG,
    }
