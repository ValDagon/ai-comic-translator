"""Account UI and delete-via-email confirmation (POST only)."""

from urllib.parse import unquote

from service import db as dbmod
from service.users import (
    PURPOSE_DELETE_ACCOUNT,
    create_email_action_token,
    get_user_by_email,
)
from tests.conftest import logout_session


def test_account_requires_login(api_client):
    client, _, _ = api_client
    logout_session(client)
    r = client.get("/account", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_account_page_tabs(api_client):
    client, _, _ = api_client
    r = client.get("/account")
    assert r.status_code == 200
    assert "Личный кабинет" in r.text
    assert "test@example.com" in r.text
    assert "Сохранить email" not in r.text
    assert "Сменить пароль" not in r.text
    assert 'href="/account?tab=subscription"' in r.text
    assert 'href="/account?tab=billing"' in r.text
    assert "/account/delete/request" in r.text

    sub = client.get("/account?tab=subscription")
    assert sub.status_code == 200
    assert "Управление подпиской появится позже" in sub.text
    assert "internal" in sub.text

    billing = client.get("/account?tab=billing")
    assert billing.status_code == 200
    assert "История платежей" in billing.text


def test_delete_account_via_email_link(api_client, monkeypatch):
    client, data_dir, get_settings = api_client
    created = client.post(
        "/ui/projects",
        data={"name": "ToDelete"},
        follow_redirects=False,
    )
    assert created.status_code == 303
    pid = unquote(created.headers["location"].split("/projects/")[1].split("/ui")[0])
    project_root = data_dir / "projects" / pid
    assert project_root.is_dir()

    captured: dict[str, str] = {}

    def _capture_email(smtp, *, to, subject, body):
        captured["body"] = body
        captured["to"] = to

    monkeypatch.setattr("web.account_routes.send_email", _capture_email)

    r = client.post(
        "/account/delete/request",
        data={"confirm": "удалить"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "notice=" in r.headers["location"]
    assert "confirm-delete?token=" in captured["body"]
    token = captured["body"].split("confirm-delete?token=")[1].split()[0].strip()

    # GET only shows confirm form — does not delete.
    page = client.get(f"/account/confirm-delete?token={token}")
    assert page.status_code == 200
    assert "confirm-delete" in page.text
    assert project_root.exists()

    confirm = client.post(
        "/account/confirm-delete",
        data={"token": token},
        follow_redirects=False,
    )
    assert confirm.status_code == 303
    assert confirm.headers["location"] == "/"

    assert not project_root.exists()
    conn = dbmod.connect(get_settings().database_path)
    try:
        assert get_user_by_email(conn, "test@example.com") is None
        assert dbmod.get_project(conn, pid) is None
    finally:
        conn.close()

    blocked = client.get("/app", follow_redirects=False)
    assert blocked.status_code == 303
    assert blocked.headers["location"].startswith("/login")


def test_delete_account_get_does_not_mutate(api_client):
    client, _, get_settings = api_client
    conn = dbmod.connect(get_settings().database_path)
    try:
        user = get_user_by_email(conn, "test@example.com")
        assert user is not None
        token = create_email_action_token(conn, user.id, PURPOSE_DELETE_ACCOUNT)
    finally:
        conn.close()

    page = client.get(f"/account/confirm-delete?token={token}")
    assert page.status_code == 200
    conn = dbmod.connect(get_settings().database_path)
    try:
        assert get_user_by_email(conn, "test@example.com") is not None
    finally:
        conn.close()


def test_delete_account_requires_confirm_word(api_client):
    client, _, _ = api_client
    r = client.post(
        "/account/delete/request",
        data={"confirm": "yes"},
    )
    assert r.status_code == 400
    assert "удалить" in r.text.lower()


def test_delete_token_invalid(api_client):
    client, _, _ = api_client
    r = client.get(
        "/account/confirm-delete?token=not-a-real-token",
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "delete_token_invalid" in r.headers["location"]


def test_create_email_action_token_roundtrip(api_client):
    client, _, get_settings = api_client
    conn = dbmod.connect(get_settings().database_path)
    try:
        user = get_user_by_email(conn, "test@example.com")
        assert user is not None
        token = create_email_action_token(conn, user.id, PURPOSE_DELETE_ACCOUNT)
        assert token
    finally:
        conn.close()
