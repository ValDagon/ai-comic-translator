from pathlib import Path

from extract_parse import (
    TextRegion,
    _natural_sort_key,
    parse_all_pages,
    parse_translations_file,
)


def test_natural_sort_key():
    names = ["10.jpg", "2.jpg", "1.jpg"]
    ordered = sorted([Path(n) for n in names], key=_natural_sort_key)
    assert [p.name for p in ordered] == ["1.jpg", "2.jpg", "10.jpg"]


def test_parse_translations_file(minimal_dir: Path):
    txt = minimal_dir / "1_translations.txt"
    page = parse_translations_file(txt, minimal_dir / "1.jpg")
    assert len(page.regions) == 2
    assert page.regions[0].text == "Hello world"
    assert page.regions[0].index == 1
    assert page.regions[1].index == 2


def test_parse_all_pages_skips_without_txt(tmp_path: Path):
    (tmp_path / "1.jpg").write_bytes(b"\xff\xd8\xff")
    (tmp_path / "2.jpg").write_bytes(b"\xff\xd8\xff")
    (tmp_path / "2_translations.txt").write_text(
        "\n[2.jpg]\n\n-- 1 --\ncolor: x\ntext:  Hi\ntrans: \ncoords: [0,0,1,0,1,1,0,1]\n",
        encoding="utf-8",
    )
    pages = parse_all_pages(tmp_path)
    assert len(pages) == 1
    assert pages[0].source_path.name == "2.jpg"


def test_parse_all_pages_natural_order(tmp_path: Path):
    for name in ("1.jpg", "10.jpg", "2.jpg"):
        (tmp_path / name).write_bytes(b"x")
        stem = Path(name).stem
        (tmp_path / f"{stem}_translations.txt").write_text(
            f"\n[{name}]\n\n-- 1 --\ncolor: x\ntext:  {stem}\ntrans: \n"
            "coords: [0,0,1,0,1,1,0,1]\n",
            encoding="utf-8",
        )
    pages = parse_all_pages(tmp_path)
    assert [p.regions[0].text for p in pages] == ["1", "2", "10"]


def test_parse_skips_empty_text_region(tmp_path: Path):
    txt = tmp_path / "1_translations.txt"
    txt.write_text(
        "\n[1.jpg]\n\n-- 1 --\ncolor: x\ntext:  \ntrans: \n"
        "coords: [0,0,1,0,1,1,0,1]\n"
        "-- 2 --\ncolor: x\ntext:  Hi\ntrans: \n"
        "coords: [0,0,1,0,1,1,0,1]\n",
        encoding="utf-8",
    )
    page = parse_translations_file(txt, tmp_path / "1.jpg")
    assert len(page.regions) == 1
    assert page.regions[0].text == "Hi"


def test_parse_skips_empty_coords(tmp_path: Path):
    txt = tmp_path / "1_translations.txt"
    txt.write_text(
        "\n[1.jpg]\n\n-- 1 --\ncolor: x\ntext:  No coords\ntrans: \n\n"
        "-- 2 --\ncolor: x\ntext:  Ok\ntrans: \n"
        "coords: [0,0,1,0,1,1,0,1]\n",
        encoding="utf-8",
    )
    page = parse_translations_file(txt, tmp_path / "1.jpg")
    assert len(page.regions) == 1
    assert page.regions[0].text == "Ok"


def test_parse_multiple_coords_lines(tmp_path: Path):
    txt = tmp_path / "1_translations.txt"
    txt.write_text(
        "\n[1.jpg]\n\n-- 1 --\ncolor: x\ntext:  Two lines\ntrans: \n"
        "coords: [0,0,1,0,1,1,0,1]\n"
        "coords: [2,2,3,2,3,3,2,3]\n",
        encoding="utf-8",
    )
    page = parse_translations_file(txt, tmp_path / "1.jpg")
    assert len(page.regions) == 1
    assert len(page.regions[0].coords) == 2


def test_parse_multiline_text(tmp_path: Path):
    txt = tmp_path / "1_translations.txt"
    txt.write_text(
        "\n[1.jpg]\n\n-- 1 --\ncolor: x\ntext:  Line one\nLine two\ntrans: \n"
        "coords: [0,0,1,0,1,1,0,1]\n",
        encoding="utf-8",
    )
    page = parse_translations_file(txt, tmp_path / "1.jpg")
    assert len(page.regions) == 1
    assert "Line one" in page.regions[0].text
    assert "Line two" in page.regions[0].text


def test_text_region_colors():
    r = TextRegion(
        1,
        "a",
        [[0, 0, 1, 0, 1, 1, 0, 1]],
        "#1: white (fg, bg: #112233 #aabbcc)",
    )
    assert r.text_colors() == ("#112233", "#aabbcc")
    assert TextRegion(1, "a", [[0, 0, 1, 0, 1, 1, 0, 1]], "x").text_colors() == (
        "#000000",
        "#ffffff",
    )
