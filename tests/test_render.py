from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from extract_parse import parse_translations_file
from render import _layout_fits, _text_width, _wrap_and_fit, _wrap_lines, render_page


def test_wrap_lines_keeps_words_whole(font_path: str):
    draw = ImageDraw.Draw(Image.new("RGB", (200, 50)))
    font = ImageFont.truetype(font_path, 16)
    text = "one two three four five six"
    lines = _wrap_lines(draw, text, font, 50)
    assert len(lines) >= 2
    joined = " ".join(lines)
    assert joined == text
    for line in lines:
        for word in line.split():
            assert word in text.split()


def test_wrap_lines_does_not_split_long_word(font_path: str):
    draw = ImageDraw.Draw(Image.new("RGB", (200, 50)))
    font = ImageFont.truetype(font_path, 16)
    word = "superlongwordwithoutspaces"
    lines = _wrap_lines(draw, word, font, 40)
    assert lines == [word]


def test_wrap_and_fit_keeps_text_inside_box(font_path: str):
    draw = ImageDraw.Draw(Image.new("RGB", (400, 200)))
    text = "Очень длинная реплика которая раньше вылезала за края облачка комикса"
    box_w, box_h = 90, 70
    font, lines, line_height = _wrap_and_fit(
        draw, text, font_path, box_w, box_h, max_size=48, min_size=6
    )
    assert lines
    assert _layout_fits(draw, lines, font, line_height, box_w, box_h)


def test_text_width_matches_bbox(font_path: str):
    draw = ImageDraw.Draw(Image.new("RGB", (200, 50)))
    font = ImageFont.truetype(font_path, 16)
    w = _text_width(draw, "Hello", font)
    assert w > 0


def test_render_page_writes_output(minimal_dir: Path, font_path: str, tmp_path: Path):
    txt = minimal_dir / "1_translations.txt"
    page = parse_translations_file(txt, minimal_dir / "1.jpg")
    clean = tmp_path / "clean.jpg"
    Image.new("RGB", (200, 120), "white").save(clean)
    out = tmp_path / "out.jpg"
    translations = {"1.1": "Test one", "1.2": "Test two"}
    render_page(page, clean, out, font_path=font_path, translations=translations, page_idx=0)
    assert out.is_file()
    assert out.stat().st_size > 500
