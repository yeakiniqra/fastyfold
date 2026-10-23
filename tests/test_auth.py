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


######################################################################
# Server-side sessions
######################################################################
def test_cookie_stops_working_after_sign_out(client, submit):
    login(client)
    stolen = client.cookies.get(SESSION_COOKIE)
    submit("/admin/logout")
    client.cookies.set(SESSION_COOKIE, stolen, domain="testserver.local", path="/admin")
    assert client.get("/admin/").status_code == HTTPStatus.SEE_OTHER


def test_signing_out_ends_only_that_browser(app, admin):
    from fastapi.testclient import TestClient

    with TestClient(app, follow_redirects=False) as laptop, TestClient(app, follow_redirects=False) as phone:
        login(laptop)
        login(phone)
        token = admin.signer.csrf(laptop.cookies.get(SESSION_COOKIE))
        laptop.post("/admin/logout", data={"_csrf": token})
        assert laptop.get("/admin/").status_code == HTTPStatus.SEE_OTHER
        assert phone.get("/admin/").status_code == HTTPStatus.OK
        laptop.portal.call(admin.engine.dispose)


def test_end_sessions_signs_out_everywhere(app, admin):
    from fastapi.testclient import TestClient

    with TestClient(app, follow_redirects=False) as a, TestClient(app, follow_redirects=False) as b:
        login(a)
        login(b)
        login(b, "editor")
        assert a.portal.call(admin.end_sessions, "admin") == 2
        assert a.get("/admin/").status_code == HTTPStatus.SEE_OTHER
        assert b.get("/admin/").status_code == HTTPStatus.OK, "editor's session is untouched"
        a.portal.call(admin.engine.dispose)


def test_expired_session_is_refused(client, db):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import update

    from fastyfold.audit import AdminSession

    login(client)

    async def expire():
        async with db.admin.session() as s:
            await s.execute(update(AdminSession).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            await s.commit()
    db.run(expire)
    assert client.get("/admin/").status_code == HTTPStatus.SEE_OTHER


def test_forged_session_id_is_refused(client, admin):
    login(client)
    client.cookies.set(SESSION_COOKIE, admin.signer.dump("admin", "made-up"), domain="testserver.local", path="/admin")
    assert client.get("/admin/").status_code == HTTPStatus.SEE_OTHER


@pytest.mark.parametrize("admin_options", [{"secure_cookies": True}])
def test_secure_cookies_behind_a_proxy(client):
    assert "Secure" in login(client).headers["set-cookie"]


######################################################################
# Error pages and headers
######################################################################
@pytest.mark.parametrize("path, code", [("/admin/secret", 403), ("/admin/post/999", 404), ("/admin/nope", 404), ("/admin/_log?page=abc", 422)])
def test_browser_errors_are_admin_pages(editor_client, path, code):
    r = editor_client.get(path, headers={"Accept": "text/html"})
    assert r.status_code == code and r.headers["content-type"].startswith("text/html")
    assert f'data-error="{code}"' in r.text and "Dashboard" in r.text


def test_api_errors_stay_json(editor_client):
    r = editor_client.get("/admin/secret")
    assert r.json() == {"detail": "You don't have permission to do that."}
    assert editor_client.get("/admin/_log?page=abc").json()["detail"][0]["loc"] == ["query", "page"]


def test_htmx_errors_stay_short(editor_client):
    r = editor_client.get("/admin/secret/1/preview", headers={"Accept": "text/html", "HX-Request": "true"})
    assert r.status_code == 403 and "data-error" not in r.text


@pytest.mark.parametrize("path", ["/admin/", "/admin/login", "/admin/post", "/admin/_search"])
def test_security_headers(client, path):
    login(client)
    headers = client.get(path).headers
    assert headers["x-frame-options"] == "DENY" and headers["content-security-policy"] == "frame-ancestors 'none'"
    assert headers["cache-control"] == "no-store" and headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "same-origin"
