"""
Полный пайплайн — CLI поверх pipeline.run_pipeline.
"""

import argparse
import os
from pathlib import Path

from config_loader import DEFAULT_CONFIG_PATH, load_settings
from pipeline import PipelineConfig, PipelineError, run_pipeline
from translate_request import (
    DEFAULT_PAGES_PER_BATCH,
    DEFAULT_TARGET_LANG,
    MODEL,
    SUPPORTED_TARGET_LANGUAGES,
    target_language,
)


def _parse_args(cfg_path: Path):
    cfg = load_settings(cfg_path)

    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, default=cfg_path,
                     help=f"путь к config.toml (по умолчанию {DEFAULT_CONFIG_PATH})")
    ap.add_argument("--mit-repo", type=Path, default=None,
                     help="путь до manga-image-translator (не нужен с --skip-extract)")
    ap.add_argument("--input", type=Path, default=Path("raw_files/pages_input"),
                     help="папка с исходными страницами на английском (50-60 файлов)")
    ap.add_argument("--out", type=Path, default=Path("results/pages_out"),
                     help="куда сложить финальные страницы с русским текстом")
    ap.add_argument("--clean-dir", type=Path, default=None,
                     help="промежуточная папка для очищенных страниц (по умолчанию --out/_clean)")
    ap.add_argument("--font", type=str, default=None,
                     help="путь до .ttf шрифта (перекрывает [render] font_path в config.toml)")
    ap.add_argument("--font-scale", type=float, default=None,
                     help="масштаб рамки под текст (перекрывает config; по умолчанию 1.2)")
    ap.add_argument("--skip-extract", action="store_true",
                     help="пропустить шаг 1, если очищенные страницы уже готовы")
    ap.add_argument("--prep-manual", action="store_true",
                     help="передать --prep-manual в manga_translator")
    ap.add_argument("--model", type=str, default=None,
                     help=f"модель OpenRouter (перекрывает config; иначе config или {MODEL})")
    ap.add_argument("--pages-per-batch", type=int, default=None,
                     help=f"страниц за запрос перевода (config или {DEFAULT_PAGES_PER_BATCH})")
    ap.add_argument("--translations-in", type=Path, default=None,
                     help="JSON с переводами — пропустить OpenRouter, только рендер")
    ap.add_argument("--translations-out", type=Path, default=None,
                     help="сохранить JSON переводов для отладки")
    ap.add_argument("--strict", action="store_true",
                     help="остановиться, если не для всех облачков есть перевод")
    ap.add_argument(
        "--target-lang",
        default=None,
        metavar="CODE",
        help="язык перевода OpenRouter (config [openrouter].target_lang или "
        + DEFAULT_TARGET_LANG
        + "): "
        + ", ".join(lang.code for lang in SUPPORTED_TARGET_LANGUAGES),
    )
    args = ap.parse_args()

    target_lang = (
        args.target_lang or cfg.openrouter.target_lang or DEFAULT_TARGET_LANG
    ).strip().lower()
    try:
        target_language(target_lang)
    except ValueError as e:
        raise SystemExit(str(e)) from e
    args.target_lang = target_lang

    font_path = args.font or cfg.render.font_path
    font_scale = (
        args.font_scale
        if args.font_scale is not None
        else (cfg.render.font_scale if cfg.render.font_scale is not None else 1.2)
    )
    model = args.model or cfg.openrouter.model or MODEL
    pages_per_batch = (
        args.pages_per_batch
        if args.pages_per_batch is not None
        else (
            cfg.openrouter.pages_per_batch
            if cfg.openrouter.pages_per_batch is not None
            else DEFAULT_PAGES_PER_BATCH
        )
    )
    api_key = cfg.openrouter.api_key or os.environ.get("OPENROUTER_API_KEY")

    return args, font_path, font_scale, model, pages_per_batch, api_key


def main():
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    pre_args, _ = pre.parse_known_args()

    args, font_path, font_scale, model, pages_per_batch, api_key = _parse_args(
        pre_args.config
    )

    if pre_args.config.is_file():
        print(f"Config: {pre_args.config.resolve()}")

    translations_out = args.translations_out
    if translations_out is None and args.translations_in is None:
        # Как API: сохраняем кэш переводов, чтобы можно было пересобрать рендер.
        translations_out = Path("results/translations.json")

    config = PipelineConfig(
        input_dir=args.input,
        out_dir=args.out,
        clean_dir=args.clean_dir,
        mit_repo=args.mit_repo,
        skip_extract=args.skip_extract,
        prep_manual=args.prep_manual,
        translations_in=args.translations_in,
        translations_out=translations_out,
        strict=args.strict,
        target_lang=args.target_lang.strip().lower(),
        font_path=font_path,
        font_scale=font_scale,
        model=model,
        pages_per_batch=pages_per_batch,
        api_key=api_key,
        project_root=Path(__file__).resolve().parent,
    )

    try:
        run_pipeline(config)
    except PipelineError as e:
        raise SystemExit(str(e)) from e


if __name__ == "__main__":
    main()
