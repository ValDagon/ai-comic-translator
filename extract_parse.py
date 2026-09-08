"""
Парсинг файлов "<страница>_translations.txt", которые создаёт
manga-image-translator (local.py::_save_text_to_file) при extract
через --save-text-file (см. run_extract.py).

Формат файла (проверено на исходниках zyddnys/manga-image-translator,
manga_translator/mode/local.py, метод _save_text_to_file):

[путь/к/странице.png]

-- 1 --
color: #1: white (fg, bg: #000000 #ffffff)
text:  Hello there!
trans:
coords: [x1, y1, x2, y2, x3, y3, x4, y4]

-- 2 --
...

При extract используем translator=original + renderer=none: поле "trans:"
может совпадать с "text:" — для пайплайна важен только "text:" (OCR),
перевод подставляем сами на шаге translate_request.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}

# MIT пишет: color: #1: white (fg, bg: #000000 #ffffff)
_COLOR_FG_BG_RE = re.compile(
    r"fg,\s*bg:\s*(#[0-9a-fA-F]{6})\s+(#[0-9a-fA-F]{6})"
)


def _natural_sort_key(path: Path) -> tuple:
    """1.jpg, 2.jpg, …, 10.jpg — не 1, 10, 11, 2."""
    parts = re.split(r"(\d+)", path.stem)
    key: list = []
    for part in parts:
        key.append(int(part) if part.isdigit() else part.lower())
    return (*key, path.suffix.lower())


@dataclass
class TextRegion:
    index: int                # номер облачка на странице (1-based, как в файле)
    text: str                 # исходный английский текст
    coords: List[List[int]]   # список полигонов [x1,y1,x2,y2,x3,y3,x4,y4] по одному на строку текста
    color_line: str           # сырая строка "color: ..." — пригодится при рендере (fg/bg)

    def bbox(self):
        """Общий прямоугольник (x_min, y_min, x_max, y_max) по всем точкам всех строк."""
        xs, ys = [], []
        for poly in self.coords:
            xs.extend(poly[0::2])
            ys.extend(poly[1::2])
        if not xs or not ys:
            raise ValueError("TextRegion without coords")
        return min(xs), min(ys), max(xs), max(ys)

    def text_colors(self) -> tuple[str, str]:
        """(fill, outline) для Pillow — fg и bg из строки color MIT."""
        m = _COLOR_FG_BG_RE.search(self.color_line)
        if m:
            return m.group(1), m.group(2)
        return "#000000", "#ffffff"


@dataclass
class Page:
    source_path: Path
    regions: List[TextRegion] = field(default_factory=list)


# text может быть многострочным до строки trans:; coords — только однострочные.
REGION_RE = re.compile(
    r"-- (\d+) --\n"
    r"color: ([^\n]*)\n"
    r"text:[ \t]*(.*?)(?=\ntrans:)"
    r"\ntrans:[ \t]*([^\n]*)\n"
    r"((?:coords: [^\n]*\n?)*)",
    re.DOTALL,
)
COORDS_RE = re.compile(r"coords:\s*\[([^\]]*)\]")


def parse_translations_file(txt_path: Path, image_path: Path) -> Page:
    content = txt_path.read_text(encoding="utf-8")
    page = Page(source_path=image_path)

    for m in REGION_RE.finditer(content):
        idx = int(m.group(1))
        color_line = m.group(2).strip()
        text = m.group(3).strip()
        coords_block = m.group(5)

        coords = []
        for cm in COORDS_RE.finditer(coords_block):
            nums = [int(n.strip()) for n in cm.group(1).split(",") if n.strip()]
            if len(nums) >= 4 and len(nums) % 2 == 0:
                coords.append(nums)

        if not text:
            # Пустые регионы (шум детектора) пропускаем
            continue
        if not coords:
            continue

        page.regions.append(
            TextRegion(index=idx, text=text, coords=coords, color_line=color_line)
        )

    return page


def find_translations_file(image_path: Path) -> Path:
    """manga-image-translator кладёт файл рядом с исходным изображением."""
    return image_path.with_name(image_path.stem + "_translations.txt")


def parse_all_pages(input_dir: Path) -> List[Page]:
    pages = []
    candidates = [
        p for p in input_dir.glob("*")
        if p.suffix.lower() in _IMAGE_SUFFIXES
    ]
    for image_path in sorted(candidates, key=_natural_sort_key):
        txt_path = find_translations_file(image_path)
        if not txt_path.exists():
            print(f"[!] Нет файла с текстом для {image_path.name}, пропускаю")
            continue
        pages.append(parse_translations_file(txt_path, image_path))
    return pages
