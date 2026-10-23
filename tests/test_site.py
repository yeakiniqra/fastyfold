import json
import re
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from fastyfold import Admin, ModelAdmin
from fastyfold.security import SESSION_COOKIE

from .factories import post
from .server.app import AuthorAdmin, PostAdmin, authenticate, create_app
from .server.models import Author, Post


@pytest.fixture
def bare(tmp_path):
    return Admin(create_async_engine("sqlite+aiosqlite://"), secret_key="k", authenticate=authenticate, media_dir=tmp_path)


######################################################################
# Registry
######################################################################
def test_register_works_as_decorator(bare):
    @bare.register
    class A(ModelAdmin):
        model = Author

    assert isinstance(bare.models["author"], A) and A.__name__ == "A"


def test_duplicate_slug(bare):
    bare.register(AuthorAdmin)
    with pytest.raises(ValueError, match="'author' is taken or reserved"):
        bare.register(AuthorAdmin)


@pytest.mark.parametrize("slug", ["login", "logout", "static", "_search", "_anything"])
def test_reserved_slugs(bare, slug):
    class A(ModelAdmin):
        model = Author

    A.slug = slug
    with pytest.raises(ValueError, match="reserved"):
        bare.register(A)


def test_secret_key_is_required():
    with pytest.raises(ValueError, match="secret_key"):
        Admin(create_async_engine("sqlite+aiosqlite://"), secret_key="", authenticate=authenticate)


def test_groups(admin):
    groups = admin.groups("admin")
    assert [(g["name"], g["icon"], [m.slug for m in g["models"]]) for g in groups] == [
        ("Content", "article", ["post", "tag"]), ("People", "person", ["author"]), ("Access", "lock", ["secret"]), ("Insights", "bar_chart", [])]
    assert [p.slug for p in admin.groups("admin")[-1]["pages"]] == ["reports", "raw"]
    assert [g["name"] for g in admin.groups("editor")] == ["Content", "People", "Insights"]


@pytest.mark.parametrize("admin_options", [{"groups": {"Content": "edit_note"}}])
def test_group_icons_option(admin):
    assert admin.groups("admin")[0]["icon"] == "edit_note"


######################################################################
# URLs
######################################################################
def test_url(admin):
    assert admin.url("") == "/admin/"
    assert admin.url() == "/admin/"
    assert admin.url("post", 3) == "/admin/post/3"
    assert admin.url("post", q="a b", page=None, sort="") == "/admin/post?q=a+b"


def test_static_files_are_served(client):
    r = client.get("/admin/static/fastyfold.css")
    assert r.status_code == HTTPStatus.OK and r.headers["content-type"].startswith("text/css")
    assert client.get("/admin/static/fastyfold.js").status_code == HTTPStatus.OK
    assert client.get("/admin/static/vendor/htmx.min.js").status_code == HTTPStatus.OK


def run_mounted(path):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    return engine, TestClient(create_app(engine, path=path), follow_redirects=False)


def test_mount_at_root():
    engine, client = run_mounted("/")
    with client as c:
        r = c.post("/login", data={"username": "admin", "password": "admin"})
        assert r.headers["location"] == "/" and "Path=/;" in r.headers["set-cookie"] + ";"
        assert c.get("/").status_code == HTTPStatus.OK
        assert c.get("/post").status_code == HTTPStatus.OK
        assert c.get("/static/fastyfold.css").status_code == HTTPStatus.OK
        c.portal.call(engine.dispose)


@pytest.mark.parametrize("next_url", ["//evil.example", "/\\evil.example", "https://evil.example"])
def test_mount_at_root_blocks_offsite_redirects(next_url):
    engine, client = run_mounted("/")
    with client as c:
        r = c.post("/login", data={"username": "admin", "password": "admin", "next": next_url})
        assert r.headers["location"] == "/"
        csrf = c.app.state.admin.signer.csrf(c.cookies.get(SESSION_COOKIE))
        r = c.post("/post/action", data={"_csrf": csrf, "action": "publish", "ids": "1", "next": next_url})
        assert r.headers["location"].startswith("/post?msg=")
        c.portal.call(engine.dispose)


def test_mount_path_trailing_slash():
    engine, client = run_mounted("/back-office/")
    with client as c:
        assert c.get("/back-office/login").status_code == HTTPStatus.OK
        assert c.get("/back-office/").headers["location"].startswith("/back-office/login")
        c.portal.call(engine.dispose)


######################################################################
# Branding and layout
######################################################################
def test_title(admin_client):
    page = admin_client.get("/admin/").text
    assert "<title>Dashboard · Test Site</title>" in page


@pytest.mark.parametrize("admin_options", [{"accent": "#2f6fed", "logo_icon": "rocket"}])
def test_accent_and_logo(client):
    page = client.get("/admin/login").text
    assert ":root { --ff-accent: #2f6fed; }" in page and ">rocket<" in page


def test_site_link_is_optional(admin_client):
    assert "View site" not in admin_client.get("/admin/").text


@pytest.mark.parametrize("admin_options", [{"site_url": "https://acme.example"}])
def test_site_link(admin_client):
    assert 'href="https://acme.example"' in admin_client.get("/admin/").text


def test_jump_keys(admin_client):
    page = admin_client.get("/admin/").text
    jumps = json.loads(re.search(r"data-jumps='([^']*)'", page).group(1))
    assert jumps == {"d": "/admin/", "p": "/admin/post", "a": "/admin/author", "r": "/admin/reports"}


def test_rail_marks_current_group(admin_client):
    page = admin_client.get("/admin/tag").text
    assert re.search(r'bg-white/10 text-white">\s*<span class="icon fill', page)


def test_audit_table_is_created_on_first_use(admin_client, db):
    db.add(post())
    assert admin_client.get("/admin/_log").status_code == HTTPStatus.OK


def test_audit_table_is_created_per_engine():
    """Engines that reuse a dead engine's id() must still get the log table."""
    for _ in range(5):
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        with TestClient(create_app(engine), follow_redirects=False) as c:
            c.post("/admin/login", data={"username": "admin", "password": "admin"})
            assert c.get("/admin/_log").status_code == HTTPStatus.OK
            c.portal.call(engine.dispose)
        del engine, c
