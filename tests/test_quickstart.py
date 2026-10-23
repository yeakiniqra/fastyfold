"""The zero-config path: Admin(engine), admin.register(Model, ...), admin.mount(app).

No secret key (one is generated and stored), no authenticate (built-in users with a first-run
setup page), and owners who manage everyone else on the Users screen.
"""

import re
from contextlib import asynccontextmanager
from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fastyfold import Admin
from fastyfold.audit import LogEntry
from fastyfold.roles import Role
from fastyfold.security import SESSION_COOKIE
from fastyfold.users import User

from .factories import post
from .fixtures import DB, make_engine
from .server.models import Author, Base, Post, Tag


def quick_app(engine) -> FastAPI:
    admin = Admin(engine, title="Quick")
    admin.register(Post, Author, Tag)

    @asynccontextmanager
    async def lifespan(app):
        async with engine.begin() as conn:
            for metadata in (LogEntry.metadata, Role.metadata, User.metadata):
                await conn.run_sync(metadata.drop_all)
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
        yield

    app = FastAPI(lifespan=lifespan)
    admin.mount(app)
    app.state.admin = admin
    return app


@pytest.fixture
def quick():
    engine = make_engine()
    app = quick_app(engine)
    with TestClient(app, follow_redirects=False) as c:
        c.db = DB(c.portal.call, app.state.admin)
        yield c
        c.portal.call(engine.dispose)


def code_of(c) -> str:
    c.get("/admin/setup")
    return c.portal.call(c.app.state.admin._setting, "setup_code")


def set_up(c, username="nusrat", password="long-password"):
    return c.post("/admin/setup", data={"code": code_of(c), "username": username, "password": password, "confirm": password})


def sign_in(c, username, password):
    c.cookies.clear()
    return c.post("/admin/login", data={"username": username, "password": password})


def csrf(c) -> str:
    return c.app.state.admin.signer.csrf(c.cookies.get(SESSION_COOKIE))


######################################################################
# First run
######################################################################
def test_first_visit_goes_to_setup(quick):
    assert quick.get("/admin/").headers["location"].startswith("/admin/login")
    assert quick.get("/admin/login").headers["location"] == "/admin/setup"
    page = quick.get("/admin/setup")
    assert page.status_code == HTTPStatus.OK and "Setup code" in page.text and "Welcome to Quick" in page.text


def test_setup_code_is_printed(quick, capsys):
    quick.get("/admin/setup")
    code = quick.portal.call(quick.app.state.admin._setting, "setup_code")
    assert re.fullmatch(r"[0-9A-F]{4}-[0-9A-F]{4}", code) and code in capsys.readouterr().out


@pytest.mark.parametrize("data, message", [
    ({"code": "WRONG-CODE"}, "isn&#39;t the code"),
    ({"password": "short", "confirm": "short"}, "at least 8 characters"),
    ({"confirm": "different-one"}, "don&#39;t match"),
    ({"username": " "}, "This field is required."),
])
def test_setup_validation(quick, data, message):
    form = {"code": code_of(quick), "username": "nusrat", "password": "long-password", "confirm": "long-password", **data}
    r = quick.post("/admin/setup", data=form)
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and message in r.text
    assert quick.db.count(User) == 0


def test_setup_creates_the_owner_and_signs_in(quick):
    r = set_up(quick)
    assert r.status_code == HTTPStatus.SEE_OTHER and r.headers["location"] == "/admin/"
    page = quick.get("/admin/").text
    assert "Good" in page and 'href="/admin/_users"' in page and 'href="/admin/_roles"' in page
    assert quick.get("/admin/post").status_code == HTTPStatus.OK, "owners see everything"
    assert quick.portal.call(quick.app.state.admin._setting, "setup_code") is None, "the code is single-use"
    assert "Never" not in quick.get("/admin/_users").text, "setup counts as the first sign-in"


def test_setup_only_once(quick):
    set_up(quick)
    quick.cookies.clear()
    assert quick.get("/admin/setup").headers["location"] == "/admin/login"
    r = quick.post("/admin/setup", data={"code": "X", "username": "intruder", "password": "long-password", "confirm": "long-password"})
    assert r.headers["location"] == "/admin/login" and quick.db.count(User) == 1


def test_setup_code_attempts_are_limited(quick):
    for _ in range(10):
        quick.post("/admin/setup", data={"code": "NOPE", "username": "x", "password": "long-password", "confirm": "long-password"})
    r = quick.post("/admin/setup", data={"code": code_of(quick), "username": "x", "password": "long-password", "confirm": "long-password"})
    assert "Too many wrong codes" in r.text and quick.db.count(User) == 0


######################################################################
# The generated secret key
######################################################################
def test_secret_key_survives_a_restart():
    engine = make_engine()
    with TestClient(quick_app(engine), follow_redirects=False) as first:
        set_up(first)
        cookie = first.cookies.get(SESSION_COOKIE)

    # The same database, a new process: the stored key still signs the old cookie. (No lifespan reset this time.)
    admin = Admin(engine)
    admin.register(Post)
    app = FastAPI()
    admin.mount(app)
    with TestClient(app, follow_redirects=False) as second:
        second.cookies.set(SESSION_COOKIE, cookie, domain="testserver.local", path="/admin")
        assert second.get("/admin/").status_code == HTTPStatus.OK
        second.portal.call(engine.dispose)


def test_explicit_secret_key_still_works(quick):
    admin = Admin(make_engine(), secret_key="mine")
    assert admin.signer.load(admin.signer.dump("u", "s")) == ("u", "s")


######################################################################
# Users screen
######################################################################
def add_user(c, username="", password="member-pass", **extra):
    return c.post("/admin/_users/new", data={"_csrf": csrf(c), "username": username, "password": password, "confirm": password, **extra})


def test_owner_adds_a_member_who_needs_a_role(quick):
    set_up(quick)
    assert add_user(quick).status_code == HTTPStatus.UNPROCESSABLE_ENTITY, "username required"
    r = add_user(quick, "lena")
    assert r.status_code == HTTPStatus.SEE_OTHER and "Saved+lena" in r.headers["location"]
    assert "No roles yet" in quick.get("/admin/_users").text

    sign_in(quick, "lena", "member-pass")
    assert quick.get("/admin/post").status_code == HTTPStatus.FORBIDDEN
    assert quick.get("/admin/_users").status_code == HTTPStatus.FORBIDDEN
    assert 'href="/admin/_users"' not in quick.get("/admin/").text and 'href="/admin/_account"' in quick.get("/admin/").text

    sign_in(quick, "nusrat", "long-password")
    quick.post("/admin/_roles/new", data={"_csrf": csrf(quick), "name": "Readers", "grant": ["post:view"]})
    quick.post("/admin/_users/2", data={"_csrf": csrf(quick), "roles": ["1"]})
    sign_in(quick, "lena", "member-pass")
    assert quick.get("/admin/post").status_code == HTTPStatus.OK
    assert quick.get("/admin/author").status_code == HTTPStatus.FORBIDDEN


def test_making_someone_an_owner(quick):
    set_up(quick)
    add_user(quick, "lena", is_owner="1")
    sign_in(quick, "lena", "member-pass")
    assert quick.get("/admin/author").status_code == HTTPStatus.OK and quick.get("/admin/_users").status_code == HTTPStatus.OK


def test_reset_password_signs_them_out(quick):
    set_up(quick)
    add_user(quick, "lena")
    owner_cookie = quick.cookies.get(SESSION_COOKIE)
    sign_in(quick, "lena", "member-pass")
    lena_cookie = quick.cookies.get(SESSION_COOKIE)
    quick.cookies.set(SESSION_COOKIE, owner_cookie, domain="testserver.local", path="/admin")
    quick.post("/admin/_users/2", data={"_csrf": csrf(quick), "password": "brand-new-pass", "confirm": "brand-new-pass"})
    quick.cookies.set(SESSION_COOKIE, lena_cookie, domain="testserver.local", path="/admin")
    assert quick.get("/admin/").status_code == HTTPStatus.SEE_OTHER
    assert sign_in(quick, "lena", "member-pass").status_code == HTTPStatus.UNAUTHORIZED
    assert sign_in(quick, "lena", "brand-new-pass").status_code == HTTPStatus.SEE_OTHER


def test_owners_cannot_lock_themselves_out(quick):
    set_up(quick)
    r = quick.post("/admin/_users/1", data={"_csrf": csrf(quick)})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "remove your own owner access" in r.text
    r = quick.post("/admin/_users/1/delete", data={"_csrf": csrf(quick)})
    assert "delete+yourself" in r.headers["location"] and quick.db.count(User) == 1


def test_delete_user(quick):
    set_up(quick)
    add_user(quick, "lena")
    r = quick.post("/admin/_users/2/delete", data={"_csrf": csrf(quick)})
    assert "Deleted+lena" in r.headers["location"] and quick.db.count(User) == 1
    assert sign_in(quick, "lena", "member-pass").status_code == HTTPStatus.UNAUTHORIZED


def test_duplicate_username(quick):
    set_up(quick)
    add_user(quick, "lena")
    r = add_user(quick, "lena")
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "already has this username" in r.text


def test_user_changes_are_audited(quick):
    set_up(quick)
    add_user(quick, "lena", is_owner="1")
    log = quick.get("/admin/_log").text
    assert "User lena" in log and "User nusrat" in log


######################################################################
# My account
######################################################################
def test_change_own_password(quick):
    set_up(quick)
    other = TestClient(quick.app, follow_redirects=False)
    other.cookies.set(SESSION_COOKIE, quick.cookies.get(SESSION_COOKIE), domain="testserver.local", path="/admin")
    r = quick.post("/admin/_account", data={"_csrf": csrf(quick), "current": "wrong", "password": "newer-password", "confirm": "newer-password"})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "isn&#39;t your current password" in r.text
    r = quick.post("/admin/_account", data={"_csrf": csrf(quick), "current": "long-password", "password": "newer-password", "confirm": "newer-password"})
    assert r.status_code == HTTPStatus.SEE_OTHER and quick.get("/admin/").status_code == HTTPStatus.OK, "this browser stays signed in"
    assert other.get("/admin/").status_code == HTTPStatus.SEE_OTHER, "other browsers are signed out"
    assert sign_in(quick, "nusrat", "long-password").status_code == HTTPStatus.UNAUTHORIZED
    assert sign_in(quick, "nusrat", "newer-password").status_code == HTTPStatus.SEE_OTHER


def test_palette_entries(quick):
    set_up(quick)
    labels = [it["label"] for g in quick.get("/admin/_search", params={"q": "us"}).json()["groups"] for it in g["items"]]
    assert "Users" in labels
    labels = [it["label"] for g in quick.get("/admin/_search", params={"q": "password"}).json()["groups"] for it in g["items"]]
    assert "Change my password" in labels


######################################################################
# Shortcuts
######################################################################
def test_register_plain_models(quick):
    admin = quick.app.state.admin
    assert set(admin.models) == {"post", "author", "tag"} and admin.models["post"].name_plural == "Posts"
    set_up(quick)
    quick.db.add(post(title="Plain"))
    assert "Plain" in quick.get("/admin/post?view=table").text


def test_database_url_instead_of_engine():
    admin = Admin("sqlite+aiosqlite://")
    assert str(admin.engine.url) == "sqlite+aiosqlite://"


def test_register_returns_what_it_got():
    admin = Admin("sqlite+aiosqlite://")
    assert admin.register(Post, Author) is Post and admin.register() is None
