from urllib.parse import unquote

from service import db as dbmod
from tests.conftest import logout_session, login_session, make_oauth_user


def test_unauthenticated_app_redirects_to_login(api_client):
    client, _, _ = api_client
    logout_session(client)
    r = client.get("/app", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_api_requires_auth(api_client):
    client, _, _ = api_client
    logout_session(client)
    r = client.get("/projects")
    assert r.status_code == 401


def test_projects_isolated_per_user(api_client):
    client, _, get_settings = api_client
    r = client.post("/ui/projects", data={"name": "OnlyA"}, follow_redirects=False)
    assert r.status_code == 303
    pid = unquote(r.headers["location"].split("/projects/")[1].split("/ui")[0])

    conn = dbmod.connect(get_settings().database_path)
    try:
        other = make_oauth_user(conn, email="other@example.com", subject="google-other")
    finally:
        conn.close()
    login_session(client, other.id)

    listed = client.get("/projects").json()
    assert all(p["id"] != pid for p in listed)
    assert client.get(f"/projects/{pid}").status_code == 404


def test_login_page_oauth_buttons_when_configured(api_client, monkeypatch):
    client, _, get_settings = api_client
    logout_session(client)

    def _settings():
        base = get_settings()
        from dataclasses import replace

        return replace(
            base,
            google_client_id="gid",
            google_client_secret="gsecret",
            apple_client_id="aid",
            apple_team_id="team",
            apple_key_id="kid",
            apple_private_key="-----BEGIN PRIVATE KEY-----\nM\n-----END PRIVATE KEY-----",
        )

    monkeypatch.setattr("api.resolve_runtime", _settings)
    monkeypatch.setattr("service.auth_deps.resolve_runtime", _settings)
    client.app.state.settings = _settings()

    r = client.get("/login")
    assert r.status_code == 200
    assert "/auth/google" in r.text
    assert "/auth/apple" in r.text
    assert 'name="password"' not in r.text


def test_oauth_google_start_unconfigured(api_client):
    client, _, _ = api_client
    logout_session(client)
    r = client.get("/auth/google", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")
    assert "oauth_google_unconfigured" in r.headers["location"]
