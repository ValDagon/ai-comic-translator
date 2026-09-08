import pytest
import requests

import translate_request
from extract_parse import Page, TextRegion
from translate_request import (
    FlatItem,
    _call_openrouter,
    _page_item_id,
    _parse_translations_json,
    _remap_translation_keys,
    find_missing_translations,
    load_translations_file,
)


def test_page_item_id():
    assert _page_item_id(0, 3) == "1.3"
    assert _page_item_id(9, 1) == "10.1"


def test_remap_translation_keys_legacy_zero_based():
    items = [
        FlatItem("1.1", 0, 1, "a"),
        FlatItem("2.2", 1, 2, "b"),
    ]
    mapped = _remap_translation_keys({"0.1": "A", "1.2": "B"}, items)
    assert mapped == {"1.1": "A", "2.2": "B"}


def test_parse_translations_json_ok():
    items = [FlatItem("1.1", 0, 1, "Hello")]
    raw = '{"translations": {"1.1": "Привет"}}'
    out = _parse_translations_json(raw, items)
    assert out["1.1"] == "Привет"


def test_parse_translations_json_empty_raises():
    items = [FlatItem("1.1", 0, 1, "Hello")]
    with pytest.raises(RuntimeError, match="политик"):
        _parse_translations_json('{"translations": {}}', items)


def test_parse_translations_json_key_mismatch_raises():
    items = [FlatItem("1.1", 0, 1, "Hello")]
    with pytest.raises(RuntimeError, match="Ни одна"):
        _parse_translations_json('{"translations": {"9.9": "x"}}', items)


def test_find_missing_translations():
    pages = [
        Page(
            source_path=__import__("pathlib").Path("1.jpg"),
            regions=[TextRegion(1, "a", [[0, 0, 1, 0, 1, 1, 0, 1]], "")],
        )
    ]
    assert find_missing_translations(pages, {}) == ["1.1"]
    assert find_missing_translations(pages, {"1.1": "x"}) == []


def test_load_translations_file(minimal_dir):
    data = load_translations_file(minimal_dir / "translations.json")
    assert data["1.1"] == "Привет мир"


def test_parse_translations_json_strips_code_fence():
    items = [FlatItem("1.1", 0, 1, "Hello")]
    raw = '```json\n{"translations": {"1.1": "Привет"}}\n```'
    out = _parse_translations_json(raw, items)
    assert out["1.1"] == "Привет"


def test_refusal_response_raises():
    items = [FlatItem("1.1", 0, 1, "Hello")]
    with pytest.raises(RuntimeError, match="отказалась"):
        _parse_translations_json(
            "I cannot translate this content due to policy.",
            items,
        )


class _FakeResponse:
    def __init__(self, status_code: int, *, text: str = "", json_data=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data
        self.headers = headers or {}

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _ok_response(item_id: str = "1.1", text: str = "Привет"):
    return _FakeResponse(
        200,
        json_data={
            "choices": [{"message": {"content": f'{{"translations": {{"{item_id}": "{text}"}}}}'}}]
        },
    )


def test_call_openrouter_retries_after_connection_error(monkeypatch):
    monkeypatch.setattr(translate_request.time, "sleep", lambda _s: None)
    items = [FlatItem("1.1", 0, 1, "Hello")]
    calls = {"n": 0}

    def fake_post(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.ConnectionError("boom")
        return _ok_response()

    monkeypatch.setattr(translate_request.requests, "post", fake_post)
    out = _call_openrouter("msg", items, api_key="k", model="m")
    assert out["1.1"] == "Привет"
    assert calls["n"] == 3


def test_call_openrouter_retries_after_5xx(monkeypatch):
    monkeypatch.setattr(translate_request.time, "sleep", lambda _s: None)
    items = [FlatItem("1.1", 0, 1, "Hello")]
    calls = {"n": 0}

    def fake_post(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 2:
            return _FakeResponse(503, text="upstream hiccup")
        return _ok_response()

    monkeypatch.setattr(translate_request.requests, "post", fake_post)
    out = _call_openrouter("msg", items, api_key="k", model="m")
    assert out["1.1"] == "Привет"
    assert calls["n"] == 2


def test_call_openrouter_gives_up_after_persistent_connection_errors(monkeypatch):
    monkeypatch.setattr(translate_request.time, "sleep", lambda _s: None)
    items = [FlatItem("1.1", 0, 1, "Hello")]

    def fake_post(*_a, **_k):
        raise requests.Timeout("still down")

    monkeypatch.setattr(translate_request.requests, "post", fake_post)
    with pytest.raises(RuntimeError, match="Не удалось связаться с OpenRouter"):
        _call_openrouter("msg", items, api_key="k", model="m")
