"""
Шаг 3: вписываем переведённый текст обратно в очищенные (пустые) облачка.

Работаем не через внутренний рендерер manga-image-translator (он заточен
под --load-text с довольно хрупким форматом), а рисуем сами через Pillow —
у нас уже есть точные координаты облачков из extract_parse.py, это надёжнее.
"""

from pathlib import Path
from typing import Dict

from PIL import Image, ImageDraw, ImageFont

from extract_parse import Page, TextRegion

DEFAULT_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

# Запас под обводку ±1px и погрешность метрик шрифта.
_STROKE = 1
_FIT_SLACK = 2


class RenderError(Exception):
    """Страница не может быть отрендерена (битый/нечитаемый clean-файл и т.п.)."""


def _default_font_path() -> str:
    for path in DEFAULT_FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    raise RuntimeError(
        "Не найден шрифт для рендера. Укажите --font /path/to/font.ttf"
    )


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> float:
    if not text:
        return 0.0
    bbox = draw.textbbox((0, 0), text, font=font)
    return float(bbox[2] - bbox[0])


def _line_height(font: ImageFont.FreeTypeFont, size: int) -> int:
    """Высота строки с запасом под кириллические выносные (у/д/р) и интерлиньяж."""
    bbox = font.getbbox("ÁyдјЖ")
    base = bbox[3] - bbox[1]
    return base + max(2, size // 6)


def _usable_width(box_w: int) -> int:
    return max(4, box_w - 2 * _STROKE - _FIT_SLACK)


def _wrap_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    box_w: int,
) -> list[str]:
    """Перенос только по границам слов — слово никогда не режется по буквам."""
    max_w = _usable_width(box_w)
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        trial = (current + " " + word).strip()
        if current and _text_width(draw, trial, font) > max_w:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _layout_fits(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    line_height: int,
    box_w: int,
    box_h: int,
) -> bool:
    if not lines:
        return True
    total_h = line_height * len(lines)
    if total_h > box_h:
        return False
    limit = _usable_width(box_w)
    return all(_text_width(draw, line, font) <= limit for line in lines)


def _wrap_and_fit(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    box_w: int,
    box_h: int,
    max_size: int = 64,
    min_size: int = 6,
):
    """Подбирает наибольший размер шрифта и разбивку на строки, при которых
    текст помещается в прямоугольник box_w x box_h."""
    text = " ".join(text.split())
    if not text:
        font = ImageFont.truetype(font_path, min_size)
        return font, [], _line_height(font, min_size)

    # Верхняя оценка по высоте облачка — не начинать с гигантского кегля.
    max_size = max(min_size, min(max_size, max(box_h - 2, min_size)))

    best = None
    for size in range(max_size, min_size - 1, -1):
        font = ImageFont.truetype(font_path, size)
        lines = _wrap_lines(draw, text, font, box_w)
        line_height = _line_height(font, size)
        if _layout_fits(draw, lines, font, line_height, box_w, box_h):
            return font, lines, line_height
        best = (font, lines, line_height)

    # min_size: всё равно возвращаем лучшую разбивку (слова целиком).
    if best is None:
        font = ImageFont.truetype(font_path, min_size)
        lines = _wrap_lines(draw, text, font, box_w)
        return font, lines, _line_height(font, min_size)
    return best


def render_page(
    page: Page,
    clean_image_path: Path,
    out_path: Path,
    font_path: str | None = None,
    padding: int = 6,
    box_inflate: float = 1.2,
    translations: Dict[str, str] | None = None,
    page_idx: int = 0,
):
    font_path = font_path or _default_font_path()
    try:
        img = Image.open(clean_image_path).convert("RGB")
    except Exception as exc:
        # A single corrupt/truncated clean image must not crash the whole
        # multi-page render step — let the caller skip this page and continue.
        raise RenderError(
            f"Не удалось открыть очищенную страницу {clean_image_path}: {exc}"
        ) from exc
    draw = ImageDraw.Draw(img)
    translations = translations or {}
    img_w, img_h = img.size

    for region in page.regions:
        item_id = f"{page_idx + 1}.{region.index}"
        text = translations.get(item_id)
        if text is None:
            # старые прогоны с 0-based id страницы
            text = translations.get(f"{page_idx}.{region.index}")
        if text is None:
            print(f"[!] Нет перевода для {item_id} на {page.source_path.name}, пропускаю облачко")
            continue

        try:
            x0, y0, x1, y1 = region.bbox()
        except ValueError:
            print(f"[!] Нет координат для {item_id} на {page.source_path.name}, пропускаю")
            continue

        # Рамка вокруг ОРИГИНАЛЬНОГО английского текста обычно теснее, чем
        # реальное облачко (в котором вокруг текста есть поля). Раздуваем
        # её вокруг центра, чтобы русский текст (он в среднем длиннее)
        # не сжимался в шрифт мельче, чем нужно.
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        half_w = (x1 - x0) / 2 * box_inflate
        half_h = (y1 - y0) / 2 * box_inflate
        x0, x1 = cx - half_w, cx + half_w
        y0, y1 = cy - half_h, cy + half_h

        # Не выходим за края страницы — иначе «влезло в box», но обрезалось.
        x0 = max(0.0, x0)
        y0 = max(0.0, y0)
        x1 = min(float(img_w), x1)
        y1 = min(float(img_h), y1)

        box_w = max(10, int((x1 - x0) - 2 * padding))
        box_h = max(10, int((y1 - y0) - 2 * padding))

        font, lines, line_height = _wrap_and_fit(draw, text, font_path, box_w, box_h)
        if not lines:
            continue

        total_h = line_height * len(lines)
        # Вертикальный центр внутри padded-области.
        inner_y0 = y0 + padding
        inner_y1 = y1 - padding
        start_y = inner_y0 + max(0.0, ((inner_y1 - inner_y0) - total_h) / 2)
        fill, outline = region.text_colors()
        center_x = (x0 + x1) / 2

        for i, line in enumerate(lines):
            y = start_y + i * line_height
            # anchor=mt — центр по X, верх по Y; без съезда из-за left bearing.
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                draw.text(
                    (center_x + dx, y + dy),
                    line,
                    font=font,
                    fill=outline,
                    anchor="mt",
                )
            draw.text((center_x, y), line, font=font, fill=fill, anchor="mt")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
