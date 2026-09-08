from service.format_when import format_when


def test_format_when_iso_utc():
    assert format_when("2026-07-26T21:14:46+00:00") == format_when(
        "2026-07-26T21:14:46+00:00"
    )
    out = format_when("2026-07-26T21:14:46+00:00")
    assert out.count(".") == 2
    assert "," in out
    assert "T" not in out
    assert "+00:00" not in out


def test_format_when_naive_treated_as_utc():
    out = format_when("2026-01-02T03:04:05")
    assert out.endswith("03:04") or ":" in out
    assert "T" not in out


def test_format_when_bad_passthrough():
    assert format_when("not-a-date") == "not-a-date"
    assert format_when("") == ""
    assert format_when(None) == ""
