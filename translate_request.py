"""
Шаг 2: перевод текста комикса через OpenRouter (https://openrouter.ai).

По умолчанию страницы отправляются пачками (--pages-per-batch), чтобы
~50–60 страниц не упирались в лимит выходных токенов и чтобы глоссарий
имён переносился между пачками.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List

import requests

LogFn = Callable[[str], None]

from extract_parse import Page

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Полный список моделей: https://openrouter.ai/models
MODEL = "x-ai/grok-4.3"
DEFAULT_PAGES_PER_BATCH = 30
DEFAULT_TARGET_LANG = "ru"


@dataclass(frozen=True)
class TargetLanguage:
    code: str
    label: str
    prompt_target: str
    prompt_locale: str


SUPPORTED_TARGET_LANGUAGES: tuple[TargetLanguage, ...] = (
    TargetLanguage("ru", "Русский", "русский", "русских"),
    TargetLanguage("en", "English", "английский", "англоязычных"),
    TargetLanguage("uk", "Українська", "украинский", "украинских"),
    TargetLanguage("de", "Deutsch", "немецкий", "немецких"),
    TargetLanguage("fr", "Français", "французский", "французских"),
    TargetLanguage("es", "Español", "испанский", "испанских"),
    TargetLanguage("it", "Italiano", "итальянский", "итальянских"),
    TargetLanguage("pt", "Português", "португальский", "португальских"),
    TargetLanguage("pl", "Polski", "польский", "польских"),
    TargetLanguage("ja", "日本語", "японский", "японских"),
    TargetLanguage("ko", "한국어", "корейский", "корейских"),
    TargetLanguage("zh", "中文", "китайский", "китайских"),
)

_TARGET_BY_CODE = {lang.code: lang for lang in SUPPORTED_TARGET_LANGUAGES}


def target_language(code: str) -> TargetLanguage:
    key = (code or DEFAULT_TARGET_LANG).strip().lower()
    lang = _TARGET_BY_CODE.get(key)
    if not lang:
        supported = ", ".join(sorted(_TARGET_BY_CODE))
        raise ValueError(f"Неизвестный язык перевода «{code}». Доступно: {supported}")
    return lang


def build_system_prompt(*, target_lang: str = DEFAULT_TARGET_LANG) -> str:
    lang = target_language(target_lang)
    return f"""\
Ты профессиональный переводчик комиксов. Исходный язык любой — определи его
по тексту реплик (OCR) и переводи на {lang.prompt_target}.

Правила:
- Сохраняй тон и характер речи персонажа (сленг, грубость, вежливость и т.д.)
- Один и тот же персонаж должен звучать одинаково на всех страницах
- Имена собственные, прозвища и повторяющиеся термины переводи ОДИНАКОВО
  везде, где они встречаются — веди для себя мысленный глоссарий
- Переводи по смыслу и с интонацией, не дословно; сохраняй юмор и игру слов,
  адаптируя её под {lang.prompt_target} язык, если дословный перевод не работает
- Реплика должна помещаться в облачко разумного размера — не растягивай
  фразу без необходимости
- Если текст — это звук (BOOM, CRASH и т.п.), переводи как принятый в
  {lang.prompt_locale} комиксах аналог по смыслу, а не транслитерацию

Тебе дан пронумерованный список реплик в порядке чтения (по страницам,
затем по облачкам сверху вниз/слева направо). Каждая реплика помечена id
вида "<номер_страницы>.<номер_облачка>" — номер страницы с 1, как в заголовках.
Верни ТОЛЬКО JSON-объект вида:
{{"translations": {{"<id>": "<перевод>", ...}}}}
без каких-либо пояснений до или после JSON. Ключи в JSON должны ТОЧНО
совпадать с id из списка (например "1.2", не "0.2").
"""


SYSTEM_PROMPT = build_system_prompt()

REFUSAL_RE = re.compile(
    r"(?i)(decline|cannot assist|can't assist|I cannot|I'm unable|"
    r"не могу|отказываюсь|policy|content policy|violat)",
)

ADULT_REFUSAL_HINT = (
    "Предположительно модель отказала в переводе из‑за политики контента провайдера."
)


@dataclass
class FlatItem:
    item_id: str          # "<page_1based>.<region_1based>"
    page_idx: int         # 0-based index in full comic
    region_idx: int
    text: str


def _page_item_id(page_idx: int, region_index: int) -> str:
    return f"{page_idx + 1}.{region_index}"


def _remap_translation_keys(raw_map: dict, items: List[FlatItem]) -> Dict[str, str]:
    """Сопоставляет ключи из ответа модели с нашими id (0-based vs 1-based страница)."""
    by_key: Dict[str, str] = {str(k).strip(): v for k, v in raw_map.items()}
    out: Dict[str, str] = {}

    for item in items:
        cid = item.item_id
        if cid in by_key:
            out[cid] = by_key[cid]
            continue
        page_1, _, region = cid.partition(".")
        legacy_page0 = f"{int(page_1) - 1}.{region}"
        if legacy_page0 in by_key:
            out[cid] = by_key[legacy_page0]
            continue
        if region in by_key and sum(1 for i in items if i.item_id.endswith(f".{region}")) == 1:
            out[cid] = by_key[region]

    return out


def flatten_pages(pages: List[Page], page_index_offset: int = 0) -> List[FlatItem]:
    items = []
    for local_idx, page in enumerate(pages):
        p_idx = page_index_offset + local_idx
        for region in page.regions:
            items.append(FlatItem(
                item_id=_page_item_id(p_idx, region.index),
                page_idx=p_idx,
                region_idx=region.index,
                text=region.text,
            ))
    return items


def build_user_message(
    items: List[FlatItem],
    pages: List[Page],
    page_index_offset: int = 0,
    glossary: str | None = None,
) -> str:
    lines: List[str] = []
    if glossary:
        lines.append(glossary)
        lines.append("")
    current_page = None
    for item in items:
        if item.page_idx != current_page:
            current_page = item.page_idx
            local = current_page - page_index_offset
            lines.append(
                f"\n=== Страница {current_page + 1} "
                f"({pages[local].source_path.name}) ==="
            )
        lines.append(f"[{item.item_id}] {item.text}")
    return "\n".join(lines)


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        if raw.lower().startswith("json"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw
    return raw.strip()


def _parse_translations_json(raw: str, items: List[FlatItem]) -> Dict[str, str]:
    raw = _strip_code_fence(raw)
    if not raw:
        raise RuntimeError(
            f"Модель вернула пустой ответ вместо JSON. {ADULT_REFUSAL_HINT}"
        )

    if REFUSAL_RE.search(raw) and "translations" not in raw:
        raise RuntimeError(
            "Модель отказалась переводить этот текст (политика провайдера), "
            "а не JSON с переводами.\n\n"
            f"Фрагмент ответа:\n{raw[:800]}\n\n"
            "Это не связано с числом страниц — отказ бывает и на 1–2 страницах. "
            "Попробуйте другую модель через --model (см. openrouter.ai/models)."
        )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise RuntimeError(
                "Не удалось распарсить ответ модели как JSON.\n"
                f"Начало ответа:\n{raw[:800]}"
            )
        parsed = json.loads(match.group(0))

    translations = parsed.get("translations")
    if not isinstance(translations, dict):
        raise RuntimeError(
            'В JSON нет объекта "translations": '
            f"{list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}"
        )

    if not translations:
        hint = raw[:500] if raw else "(пусто)"
        raise RuntimeError(
            "Модель вернула пустой объект translations. "
            f"{ADULT_REFUSAL_HINT}\n"
            f"Фрагмент ответа: {hint}\n\n"
            "Смените модель в config.toml ([openrouter] model) или через --model."
        )

    raw_keys = list(translations.keys())
    translations = _remap_translation_keys(translations, items)
    missing = [item.item_id for item in items if item.item_id not in translations]
    if len(missing) == len(items):
        raise RuntimeError(
            f"Ни одна из {len(items)} реплик не получила перевод.\n"
            f"Ключи в ответе модели: {raw_keys[:25]}\n"
            "Ожидались id вида 1.1, 2.3 — проверьте формат или смените модель."
        )

    if missing:
        print(
            f"[!] Модель не вернула перевод для {len(missing)} id "
            f"(облачка останутся пустыми): {', '.join(missing[:8])}"
            + (" …" if len(missing) > 8 else "")
        )
    unchanged = [
        item.item_id
        for item in items
        if item.item_id in translations
        and translations[item.item_id].strip() == item.text.strip()
    ]
    if unchanged:
        print(
            f"[!] {len(unchanged)} реплик в ответе совпали с английским OCR "
            f"(проверьте ключи id): {', '.join(unchanged[:8])}"
            + (" …" if len(unchanged) > 8 else "")
        )

    return translations


def _estimate_max_tokens(item_count: int) -> int:
    # ~80–120 токенов на реплику в JSON; верхний предел для OpenRouter
    return min(32_000, max(4000, item_count * 120))


def _call_openrouter(
    user_message: str,
    items: List[FlatItem],
    *,
    api_key: str,
    model: str,
    target_lang: str = DEFAULT_TARGET_LANG,
) -> Dict[str, str]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/",
        "X-Title": "comic-translator",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": build_system_prompt(target_lang=target_lang)},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": _estimate_max_tokens(len(items)),
        "response_format": {"type": "json_object"},
    }

    max_attempts = 5
    resp = None
    use_json_mode = True
    for attempt in range(1, max_attempts + 1):
        body = payload if use_json_mode else {
            k: v for k, v in payload.items() if k != "response_format"
        }
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=body, timeout=300)
        except requests.RequestException as exc:
            print(
                f"[!] Сетевая ошибка при обращении к OpenRouter "
                f"(попытка {attempt}/{max_attempts}): {exc}"
            )
            if attempt < max_attempts:
                wait_s = min(60, 5 * 2 ** (attempt - 1))
                print(f"    жду {wait_s:.0f} сек и пробую снова...")
                time.sleep(wait_s)
                continue
            raise RuntimeError(
                f"Не удалось связаться с OpenRouter после {max_attempts} попыток: {exc}"
            ) from exc

        if resp.status_code == 200:
            break
        if resp.status_code == 400 and use_json_mode and attempt == 1:
            use_json_mode = False
            print("[!] Модель не поддерживает response_format=json_object, повтор без него")
            continue

        print(
            f"[!] OpenRouter вернул {resp.status_code} "
            f"(попытка {attempt}/{max_attempts}): {resp.text[:500]}"
        )

        # 429 (rate limit) and 5xx (transient server-side errors) are worth
        # retrying; a whole multi-page batch shouldn't be lost to a blip.
        retryable = resp.status_code == 429 or resp.status_code >= 500
        if retryable and attempt < max_attempts:
            retry_after = resp.headers.get("Retry-After")
            wait_s = float(retry_after) if retry_after else min(60, 5 * 2 ** (attempt - 1))
            print(f"    жду {wait_s:.0f} сек и пробую снова...")
            time.sleep(wait_s)
            continue

        resp.raise_for_status()
        break

    if resp is None:
        raise RuntimeError("Не удалось получить ответ от OpenRouter")

    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(
            "OpenRouter вернул ответ без choices: "
            f"{json.dumps(data, ensure_ascii=False)[:800]}"
        )
    raw = choices[0].get("message", {}).get("content") or ""
    if not str(raw).strip():
        raise RuntimeError(
            f"Модель вернула пустой ответ (нет текста в message.content). {ADULT_REFUSAL_HINT}"
        )
    try:
        return _parse_translations_json(raw, items)
    except RuntimeError:
        debug_path = Path(os.environ.get("OPENROUTER_DEBUG_RAW", "results/openrouter_last_raw.txt"))
        try:
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(raw, encoding="utf-8")
            print(f"[!] Сырой ответ модели сохранён в {debug_path.resolve()}")
        except OSError:
            pass
        raise


def _build_glossary(
    prior: Dict[str, str],
    items: List[FlatItem],
    max_lines: int = 40,
) -> str | None:
    if not prior:
        return None
    lines = ["=== Уже переведено ранее (те же имена и термины) ==="]
    shown = 0
    for item in items:
        ru = prior.get(item.item_id)
        if ru is None or ru == item.text:
            continue
        lines.append(f"[{item.item_id}] {item.text!r} → {ru!r}")
        shown += 1
        if shown >= max_lines:
            break
    if shown == 0:
        # передаём любые последние переводы из prior для тона/имён
        for item_id, ru in list(prior.items())[-max_lines:]:
            lines.append(f"[{item_id}] → {ru!r}")
            shown += 1
    return "\n".join(lines) if shown else None


def translate_batch(
    pages: List[Page],
    page_index_offset: int = 0,
    *,
    api_key: str,
    model: str = MODEL,
    glossary: str | None = None,
    target_lang: str = DEFAULT_TARGET_LANG,
) -> Dict[str, str]:
    items = flatten_pages(pages, page_index_offset=page_index_offset)
    if not items:
        return {}
    user_message = build_user_message(
        items, pages, page_index_offset=page_index_offset, glossary=glossary,
    )
    return _call_openrouter(
        user_message,
        items,
        api_key=api_key,
        model=model,
        target_lang=target_lang,
    )


def translate_all(
    pages: List[Page],
    api_key: str | None = None,
    model: str = MODEL,
    pages_per_batch: int = DEFAULT_PAGES_PER_BATCH,
    log: LogFn | None = None,
    target_lang: str = DEFAULT_TARGET_LANG,
    checkpoint_path: Path | None = None,
) -> Dict[str, str]:
    """Возвращает словарь {item_id: перевод} для всех страниц."""
    _log = log or print
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "Не задан ключ OpenRouter: укажите [openrouter] api_key в config.toml "
            "или export OPENROUTER_API_KEY=sk-or-..."
        )

    if not pages:
        return {}

    config_cap = pages_per_batch
    if pages_per_batch <= 0 or pages_per_batch >= len(pages):
        pages_per_batch = len(pages)

    merged: Dict[str, str] = {}
    if checkpoint_path and checkpoint_path.is_file():
        try:
            merged.update(load_translations_file(checkpoint_path))
            if merged:
                _log(f"  checkpoint: загружено {len(merged)} переводов из {checkpoint_path.name}")
        except Exception as exc:
            _log(f"  checkpoint: не удалось прочитать ({exc}), начинаю с нуля")

    total_batches = (len(pages) + pages_per_batch - 1) // pages_per_batch
    if total_batches == 1:
        _log(
            f"  перевод: {len(pages)} стр. одним запросом "
            f"(в config pages_per_batch={config_cap}, сейчас не дробим)"
        )
    else:
        _log(
            f"  перевод: {len(pages)} стр., пачки до {pages_per_batch} стр. "
            f"(config pages_per_batch={config_cap})"
        )

    for batch_no, start in enumerate(range(0, len(pages), pages_per_batch), start=1):
        chunk = pages[start : start + pages_per_batch]
        needed = [
            item.item_id
            for item in flatten_pages(chunk, start)
            if item.item_id not in merged
        ]
        if not needed and merged:
            if total_batches > 1:
                _log(
                    f"  пачка {batch_no}/{total_batches}: "
                    f"страницы {start + 1}–{start + len(chunk)} — уже в checkpoint, пропускаю"
                )
            continue
        if total_batches > 1:
            _log(
                f"  пачка {batch_no}/{total_batches}: "
                f"страницы {start + 1}–{start + len(chunk)} из {len(pages)} "
                f"({len(chunk)} шт.)"
            )
        glossary = _build_glossary(merged, flatten_pages(chunk, start)) if merged else None
        batch = translate_batch(
            chunk,
            page_index_offset=start,
            api_key=key,
            model=model,
            glossary=glossary,
            target_lang=target_lang,
        )
        merged.update(batch)
        if checkpoint_path:
            write_translations_file(checkpoint_path, merged)

    return merged


def expected_item_ids(pages: List[Page]) -> List[str]:
    return [item.item_id for item in flatten_pages(pages)]


def find_missing_translations(pages: List[Page], translations: Dict[str, str]) -> List[str]:
    return [iid for iid in expected_item_ids(pages) if iid not in translations]


def load_translations_file(path: Path) -> Dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("translations", data)
    if not isinstance(raw, dict):
        raise RuntimeError(f'В {path} ожидается объект "translations": {{...}}')
    return {str(k).strip(): v for k, v in raw.items()}


def write_translations_file(path: Path, translations: Dict[str, str]) -> None:
    """Атомарная запись translations.json (temp + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"translations": translations}, ensure_ascii=False, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
