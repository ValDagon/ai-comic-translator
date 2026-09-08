"""Google / Apple OAuth (authorization code)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import jwt
from authlib.integrations.requests_client import OAuth2Session

from service.users import PROVIDER_APPLE, PROVIDER_GOOGLE

GOOGLE_AUTHORIZE = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"

APPLE_AUTHORIZE = "https://appleid.apple.com/auth/authorize"
APPLE_TOKEN = "https://appleid.apple.com/auth/token"
APPLE_JWKS = "https://appleid.apple.com/auth/keys"
APPLE_ISSUER = "https://appleid.apple.com"


@dataclass(frozen=True)
class OAuthSettings:
    public_base_url: str
    google_client_id: str
    google_client_secret: str
    apple_client_id: str
    apple_team_id: str
    apple_key_id: str
    apple_private_key: str

    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    def apple_enabled(self) -> bool:
        return bool(
            self.apple_client_id
            and self.apple_team_id
            and self.apple_key_id
            and self.apple_private_key
        )

    def callback_url(self, provider: str) -> str:
        base = self.public_base_url.rstrip("/")
        return f"{base}/auth/{provider}/callback"


@dataclass(frozen=True)
class OAuthProfile:
    provider: str
    subject: str
    email: str


class OAuthConfigError(RuntimeError):
    pass


def _google_session(settings: OAuthSettings) -> OAuth2Session:
    if not settings.google_enabled():
        raise OAuthConfigError("err.oauth_google_unconfigured")
    return OAuth2Session(
        settings.google_client_id,
        settings.google_client_secret,
        scope="openid email profile",
        redirect_uri=settings.callback_url(PROVIDER_GOOGLE),
    )


def google_authorize_url(settings: OAuthSettings, *, state: str) -> str:
    client = _google_session(settings)
    uri, _ = client.create_authorization_url(
        GOOGLE_AUTHORIZE,
        state=state,
        access_type="online",
        prompt="select_account",
    )
    return uri


def google_fetch_profile(
    settings: OAuthSettings, *, code: str, state: str | None = None
) -> OAuthProfile:
    client = _google_session(settings)
    client.fetch_token(
        GOOGLE_TOKEN,
        code=code,
        grant_type="authorization_code",
    )
    resp = client.get(GOOGLE_USERINFO)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    sub = str(data.get("sub") or "").strip()
    email = str(data.get("email") or "").strip()
    if not sub or not email:
        raise ValueError("err.oauth_profile")
    if data.get("email_verified") is not True:
        raise ValueError("err.oauth_email_unverified")
    return OAuthProfile(provider=PROVIDER_GOOGLE, subject=sub, email=email)


def _apple_client_secret(settings: OAuthSettings) -> str:
    now = int(time.time())
    headers = {"kid": settings.apple_key_id}
    payload = {
        "iss": settings.apple_team_id,
        "iat": now,
        "exp": now + 86000,
        "aud": "https://appleid.apple.com",
        "sub": settings.apple_client_id,
    }
    return jwt.encode(
        payload,
        settings.apple_private_key,
        algorithm="ES256",
        headers=headers,
    )


def apple_authorize_url(settings: OAuthSettings, *, state: str) -> str:
    if not settings.apple_enabled():
        raise OAuthConfigError("err.oauth_apple_unconfigured")
    params = {
        "client_id": settings.apple_client_id,
        "redirect_uri": settings.callback_url(PROVIDER_APPLE),
        "response_type": "code",
        "response_mode": "form_post",
        "scope": "name email",
        "state": state,
    }
    return f"{APPLE_AUTHORIZE}?{urlencode(params)}"


def apple_fetch_profile(
    settings: OAuthSettings,
    *,
    code: str,
    id_token_hint: str | None = None,
) -> OAuthProfile:
    if not settings.apple_enabled():
        raise OAuthConfigError("err.oauth_apple_unconfigured")
    client = OAuth2Session(
        settings.apple_client_id,
        _apple_client_secret(settings),
        redirect_uri=settings.callback_url(PROVIDER_APPLE),
    )
    token = client.fetch_token(
        APPLE_TOKEN,
        code=code,
        grant_type="authorization_code",
    )
    # Identity must come from the token endpoint response, never from form fields.
    id_token = token.get("id_token")
    if not id_token:
        raise ValueError("err.oauth_profile")
    del id_token_hint  # unused; kept in signature for call-site compatibility
    claims = _verify_apple_id_token(id_token, audience=settings.apple_client_id)
    sub = str(claims.get("sub") or "").strip()
    email = str(claims.get("email") or "").strip()
    if not sub or not email:
        raise ValueError("err.oauth_profile")
    if claims.get("email_verified") is False:
        raise ValueError("err.oauth_email_unverified")
    return OAuthProfile(provider=PROVIDER_APPLE, subject=sub, email=email)


def _verify_apple_id_token(id_token: str, *, audience: str) -> dict[str, Any]:
    jwks_client = jwt.PyJWKClient(APPLE_JWKS)
    signing_key = jwks_client.get_signing_key_from_jwt(id_token)
    return jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=audience,
        issuer=APPLE_ISSUER,
    )
