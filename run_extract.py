"""
Шаг 1: детекция текста + OCR + инпейнтинг (удаление оригинального текста
из облачков) через manga-image-translator.

Требует отдельно установленный и склонированный репозиторий:
    git clone https://github.com/zyddnys/manga-image-translator.git
    cd manga-image-translator
    pip install -r requirements.txt --break-system-packages

    В текущей версии репозитория выбор переводчика (translator=original +
    renderer=none: OCR+инпейнтинг без встроенного перевода MIT) задаётся не
    флагом командной строки, а через --config-file с JSON-конфигом — поэтому
    мы создаём временный config.json на лету. Это даёт нам:
  - чистые страницы с пустыми облачками (готовая "подложка" под русский текст)
  - файлы <страница>_translations.txt с исходным текстом и координатами
    (см. extract_parse.py)
"""

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tomllib
from collections.abc import Callable
from pathlib import Path

LogFn = Callable[[str], None]

# No output at all for this long ⇒ treat the subprocess as hung (GPU/CPU
# stall, deadlock) and kill it, instead of blocking the job/worker forever.
_MIT_IDLE_TIMEOUT_SEC = 20 * 60

# batch>=2 is faster; requires translator!=none (see mit_extract_config.toml).
_MIT_EXTRACT_BATCH_SIZE = 2

# MIT --save-text calls exit(-1) after translation (rc 255) and skips inpaint.
# --save-text-file is truthy for local.py dumps but does not take that abort path.
# The path value is unused: dumps still go next to each input as *_translations.txt.
_MIT_SAVE_TEXT_FILE_MARKER = "ai-comic-translator-save-text"

_MIT_CONFIG = Path(__file__).resolve().parent / "mit_extract_config.toml"
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _mit_extractor_config() -> dict:
    """Конфиг manga-image-translator: mit_extract_config.toml или значения по умолчанию."""
    if _MIT_CONFIG.is_file():
        raw = tomllib.loads(_MIT_CONFIG.read_text(encoding="utf-8"))
        tr = raw.get("translator") or {}
        rd = raw.get("render") or {}
        return {
            "translator": {
                "translator": tr.get("translator", "original"),
                "target_lang": tr.get("target_lang", "RUS"),
                "no_text_lang_skip": tr.get("no_text_lang_skip", True),
            },
            "render": {
                "renderer": rd.get("renderer", "none"),
            },
        }
    return {
        "translator": {
            "translator": "original",
            "target_lang": "RUS",
            "no_text_lang_skip": True,
        },
        "render": {
            "renderer": "none",
        },
    }


def _count_page_images(input_dir: Path) -> int:
    return sum(
        1
        for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_SUFFIXES and not p.name.startswith(".")
    )


def _count_translation_dumps(input_dir: Path) -> int:
    return sum(1 for _ in input_dir.glob("*_translations.txt"))


def _pipe_reader(pipe, q: "queue.Queue[str | None]") -> None:
    try:
        for line in pipe:
            q.put(line)
    finally:
        q.put(None)  # EOF sentinel


def _kill_proc(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run_extraction(
    mit_repo: Path,
    input_dir: Path,
    clean_dir: Path,
    python_bin: str = sys.executable,
    prep_manual: bool = False,
    log: LogFn | None = None,
) -> None:
    """
    mit_repo    — путь до склонированного manga-image-translator
    input_dir   — папка с исходными страницами (jpg/png), 50-60 штук
    clean_dir   — куда сложить очищенные страницы (без текста)
    prep_manual — если True, добавляет флаг --prep-manual: заставляет
                  manga_translator сохранять и текст, и рядом версию
                  страницы без текста даже для тех облачков, где обычный
                  dump почему-то ничего не выгрузил. Полезно
                  включить, если после обычного запуска pages в main.py
                  оказались пустыми.
    """
    clean_dir.mkdir(parents=True, exist_ok=True)

    config = _mit_extractor_config()
    config_fd = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="mit_config_", delete=False, encoding="utf-8"
    )
    json.dump(config, config_fd)
    config_fd.close()
    config_path = Path(config_fd.name)

    cmd = [
        python_bin, "-m", "manga_translator", "local",
        "-i", str(input_dir),
        "-o", str(clean_dir),
        "--config-file", str(config_path),
        "--save-text-file", _MIT_SAVE_TEXT_FILE_MARKER,
        "--overwrite",
        "--batch-size", str(_MIT_EXTRACT_BATCH_SIZE),
    ]
    if prep_manual:
        cmd.append("--prep-manual")

    _log = log or print
    _log("Запускаю: " + " ".join(cmd))
    _log("(extract может занять несколько минут — строки MIT ниже по мере готовности)")
    proc: subprocess.Popen | None = None
    try:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            cmd,
            cwd=str(mit_repo),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        assert proc.stdout is not None

        # Read via a background thread + queue (not a blocking `for line in
        # proc.stdout`) so a hung child that never writes/exits doesn't block
        # this call — and therefore the worker process — forever.
        q: "queue.Queue[str | None]" = queue.Queue()
        reader = threading.Thread(
            target=_pipe_reader, args=(proc.stdout, q), daemon=True
        )
        reader.start()

        while True:
            try:
                line = q.get(timeout=_MIT_IDLE_TIMEOUT_SEC)
            except queue.Empty:
                _kill_proc(proc)
                raise RuntimeError(
                    "manga-image-translator не выдавал вывод "
                    f"{_MIT_IDLE_TIMEOUT_SEC // 60} мин — похоже на зависание "
                    "(GPU/CPU стопор, дедлок). Процесс остановлен, запустите job снова."
                )
            if line is None:
                break
            _log(line.rstrip())

        try:
            rc = proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            _kill_proc(proc)
            raise RuntimeError(
                "manga-image-translator не завершился после закрытия вывода — "
                "процесс остановлен принудительно."
            ) from None
        if rc != 0:
            raise RuntimeError(f"manga-image-translator завершился с кодом {rc}")
    finally:
        config_path.unlink(missing_ok=True)
        if proc is not None:
            _kill_proc(proc)

    n_images = _count_page_images(input_dir)
    n_txt = _count_translation_dumps(input_dir)
    if n_images and n_txt == 0:
        raise RuntimeError(
            "manga-image-translator завершился, но не создал *_translations.txt "
            f"в {input_dir} (страниц: {n_images}). Проверьте mit_extract_config.toml: "
            "нужен translator=original (не none) при --batch-size >= 2, иначе MIT "
            "отфильтровывает все OCR-регионы и dump текста ничего не пишет."
        )
    if n_images and n_txt < n_images:
        raise RuntimeError(
            "manga-image-translator завершился неполно: "
            f"страниц-изображений {n_images}, дампов *_translations.txt {n_txt} "
            f"в {input_dir}. Запустите extract снова или проверьте проблемные страницы."
        )

    _log(
        f"Готово. Очищенные страницы — в {clean_dir}; "
        f"*_translations.txt — рядом с файлами в {input_dir} ({n_txt} шт.)"
    )


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--mit-repo", type=Path, required=True, help="путь до manga-image-translator")
    ap.add_argument("--input", type=Path, required=True, help="папка со страницами на английском")
    ap.add_argument("--clean", type=Path, required=True, help="куда сохранить очищенные страницы")
    ap.add_argument("--prep-manual", action="store_true",
                     help="передать --prep-manual в manga_translator (см. run_extraction)")
    args = ap.parse_args()

    run_extraction(args.mit_repo, args.input, args.clean, prep_manual=args.prep_manual)
