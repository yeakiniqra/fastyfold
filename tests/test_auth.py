from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

import pytest

from fastyfold.security import SESSION_COOKIE

from .fixtures import login


def next_of(location: str) -> str:
    return parse_qs(urlsplit(location).query)["next"][0]


######################################################################
# Anonymous access
######################################################################
def test_login_page(client):
    r = client.get("/admin/login")
    assert r.status_code == HTTPStatus.OK
    assert 'name="username"' in r.text and 'name="password"' in r.text and "Test Site" in r.text


@pytest.mark.parametrize("path", ["/admin/", "/admin/post", "/admin/post/new", "/admin/post/1", "/admin/post/1/preview",
                                  "/admin/_log", "/admin/_search"])
def test_anonymous_is_sent_to_login(client, path):
    r = client.get(path)
    assert r.status_code == HTTPStatus.SEE_OTHER
    assert r.headers["location"].startswith("/admin/login?")
    assert next_of(r.headers["location"]) == path


def test_anonymous_htmx_request_gets_hx_redirect(client):
    r = client.get("/admin/post/1/preview", headers={"HX-Request": "true"})
    assert r.status_code == HTTPStatus.UNAUTHORIZED
    assert next_of(r.headers["HX-Redirect"]) == "/admin/post/1/preview"


def test_anonymous_post_is_rejected(client, db):
    r = client.post("/admin/post/new", data={"title": "x"})
    assert r.status_code in (HTTPStatus.FORBIDDEN, HTTPStatus.SEE_OTHER)
    assert r.status_code != HTTPStatus.OK


######################################################################
# Sign in
######################################################################
@pytest.mark.parametrize("username, password", [("admin", "nope"), ("ghost", "ghost"), ("", "")])
def test_wrong_credentials(client, username, password):
    r = client.post("/admin/login", data={"username": username, "password": password})
    assert r.status_code == HTTPStatus.UNAUTHORIZED
    assert "password didn&#39;t match" in r.text
    assert SESSION_COOKIE not in r.cookies


def test_wrong_credentials_keep_username(client):
    r = client.post("/admin/login", data={"username": "admin", "password": "nope", "next": "/admin/post"})
    assert 'value="admin"' in r.text and 'value="/admin/post"' in r.text


def test_sign_in_sets_session_cookie(client):
    r = login(client)
    assert r.headers["location"] == "/admin/"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/admin" in cookie
    assert "Max-Age" not in cookie, "without 'remember' the cookie ends with the browser session"
    assert client.get("/admin/").status_code == HTTPStatus.OK


def test_remember_me_keeps_cookie_for_two_weeks(client):
    r = login(client, remember="1")
    assert "Max-Age=1209600" in r.headers["set-cookie"]


@pytest.mark.parametrize("next_url, target", [
    ("/admin/post?view=board", "/admin/post?view=board"),
    ("/admin/author/3", "/admin/author/3"),
    ("", "/admin/"),
    ("/admin", "/admin/"),
    ("/elsewhere", "/admin/"),
    ("https://evil.example/admin/", "/admin/"),
    ("//evil.example", "/admin/"),
])
def test_sign_in_redirect_stays_inside_admin(client, next_url, target):
    assert login(client, next=next_url).headers["location"] == target


######################################################################
# Sign out
######################################################################
def test_sign_out(admin_client, submit):
    r = submit("/admin/logout")
    assert r.status_code == HTTPStatus.SEE_OTHER and r.headers["location"] == "/admin/login"
    assert f'{SESSION_COOKIE}=""' in r.headers["set-cookie"]
    assert admin_client.get("/admin/").status_code == HTTPStatus.SEE_OTHER


def test_sign_out_needs_csrf(admin_client):
    assert admin_client.post("/admin/logout").status_code == HTTPStatus.FORBIDDEN
    assert admin_client.get("/admin/").status_code == HTTPStatus.OK


def test_user_shown_in_rail(admin_client):
    assert 'title="Sign out admin"' in admin_client.get("/admin/").text


######################################################################
# Rate limiting
######################################################################
def fail(client, n):
    for _ in range(n):
        assert client.post("/admin/login", data={"username": "admin", "password": "nope"}).status_code == HTTPStatus.UNAUTHORIZED


def test_too_many_failures_block_sign_in(client):
    fail(client, 10)
    r = client.post("/admin/login", data={"username": "admin", "password": "admin"})
    assert r.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert "Too many failed sign-ins" in r.text and SESSION_COOKIE not in r.cookies


def test_success_resets_the_count(client):
    fail(client, 9)
    login(client)
    fail(client, 9)
    assert login(client).status_code == HTTPStatus.SEE_OTHER


def test_block_expires(client, monkeypatch):
    import fastyfold.admin

    fail(client, 10)
    later = fastyfold.admin.time.monotonic() + 15 * 60 + 1
    monkeypatch.setattr(fastyfold.admin.time, "monotonic", lambda: later)
    assert login(client).status_code == HTTPStatus.SEE_OTHER


@pytest.mark.parametrize("admin_options", [{"login_attempts": 2}])
def test_limit_is_configurable(client):
    fail(client, 2)
    assert client.post("/admin/login", data={"username": "admin", "password": "admin"}).status_code == HTTPStatus.TOO_MANY_REQUESTS


@pytest.mark.parametrize("admin_options", [{"login_attempts": 0}])
def test_limit_can_be_turned_off(client):
    fail(client, 30)
    assert login(client).status_code == HTTPStatus.SEE_OTHER
