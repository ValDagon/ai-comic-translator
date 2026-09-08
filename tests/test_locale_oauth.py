"""Locale catalog key parity and OAuth authorize smoke."""

from __future__ import annotations

import json

from service.oauth import OAuthSettings, google_authorize_url
from web.i18n import LOCALES_DIR, SUPPORTED_LOCALES


def test_locale_keys_match_english():
    en = json.loads((LOCALES_DIR / "en.json").read_text(encoding="utf-8"))
    en_keys = set(en)
    for code in SUPPORTED_LOCALES:
        if code == "en":
            continue
        data = json.loads((LOCALES_DIR / f"{code}.json").read_text(encoding="utf-8"))
        missing = en_keys - set(data)
        extra = set(data) - en_keys
        assert not missing, f"{code} missing keys: {sorted(missing)[:20]}"
        assert not extra, f"{code} extra keys: {sorted(extra)[:20]}"


def test_google_authorize_url_contains_state():
    settings = OAuthSettings(
        public_base_url="https://comics.example.com",
        google_client_id="cid",
        google_client_secret="sec",
        apple_client_id="",
        apple_team_id="",
        apple_key_id="",
        apple_private_key="",
    )
    url = google_authorize_url(settings, state="abc123")
    assert "accounts.google.com" in url
    assert "state=abc123" in url
    assert "comics.example.com" in url


def test_oauth_callback_rejects_bad_state(api_client):
    client, _, _ = api_client
    r = client.get(
        "/auth/google/callback?code=x&state=wrong",
        follow_redirects=False,
    )
    assert r.status_code == 303
    loc = r.headers["location"]
    assert "oauth_state" in loc or loc.startswith("/login")
