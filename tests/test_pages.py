"""Custom pages from @admin.page and dashboard widgets from @admin.widget."""

from http import HTTPStatus

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from fastyfold import Admin

from .factories import author, post
from .server.app import authenticate
from .server.models import Status


######################################################################
# Pages
######################################################################
def test_page_renders_inside_layout(admin_client):
    r = admin_client.get("/admin/reports")
    assert r.status_code == HTTPStatus.OK
    assert "<title>Reports · Test Site</title>" in r.text and "<p id='hello'>Hello admin</p>" in r.text
    assert 'data-jumps=' in r.text, "it is the full layout"


def test_page_post_needs_csrf(admin_client, submit):
    assert admin_client.post("/admin/reports", data={"note": "x"}).status_code == HTTPStatus.FORBIDDEN
    r = submit("/admin/reports", {"note": "<b>hi</b>"})
    assert r.status_code == HTTPStatus.OK and "<p id='echo'>Got &lt;b&gt;hi&lt;/b&gt;</p>" in r.text


def test_page_can_return_a_response(admin_client):
    assert admin_client.get("/admin/raw").json() == {"user": "admin"}


def test_pages_in_navigation(admin_client):
    page = admin_client.get("/admin/").text
    assert 'href="/admin/reports"' in page and "Insights" in page
    labels = {it["label"]: it for g in admin_client.get("/admin/_search", params={"q": "rep"}).json()["groups"] for it in g["items"]}
    assert labels["Reports"]["url"] == "/admin/reports" and labels["Reports"]["keys"] == ["G", "R"]


def test_current_page_is_highlighted(admin_client):
    page = admin_client.get("/admin/reports").text
    assert 'href="/admin/reports" class="flex items-center gap-2.5 rounded-xl px-2 py-2 font-medium bg-panel' in page


def no_reports(user, perm, item):
    return item.slug != "reports"


@pytest.mark.parametrize("admin_options", [{"permission": no_reports}])
def test_page_permission(admin_client):
    assert admin_client.get("/admin/reports").status_code == HTTPStatus.FORBIDDEN
    assert 'href="/admin/reports"' not in admin_client.get("/admin/").text


def test_page_needs_sign_in(client):
    assert client.get("/admin/reports").status_code == HTTPStatus.SEE_OTHER


def test_post_to_a_model_list_is_not_allowed(submit, admin_client):
    assert submit("/admin/post").status_code == HTTPStatus.METHOD_NOT_ALLOWED


@pytest.mark.parametrize("slug", ["post", "login", "_x"])
def test_page_slug_must_be_free(admin, slug):
    with pytest.raises(ValueError, match="taken or reserved"):
        admin.page("Clash", slug=slug)(lambda request, user: "")


def test_page_slug_from_name():
    admin = Admin(create_async_engine("sqlite+aiosqlite://"), secret_key="k", authenticate=authenticate)
    admin.page("Sales & Growth!")(lambda request, user: "")
    assert list(admin.pages) == ["sales-growth"]


######################################################################
# Widgets
######################################################################
def test_widgets(admin_client, db):
    db.add(post(views=10, status=Status.published), post(views=5), author())
    page = admin_client.get("/admin/").text
    assert page.count("data-widget") == 4
    assert "Total views" in page and ">15</div>" in page and "across all posts" in page
    assert "Authors" in page and ">3</div>" in page
    assert "Views by status" in page and "Published" in page and "width: 100.0%" in page and "width: 50.0%" in page
    assert "Only the owner sees this" in page


def test_widget_can_hide_itself(editor_client):
    page = editor_client.get("/admin/").text
    assert page.count("data-widget") == 3 and "Only the owner sees this" not in page


def test_widget_spans(admin_client):
    page = admin_client.get("/admin/").text
    assert "xl:col-span-3\" data-widget" in page and "xl:col-span-4\" data-widget" in page and "xl:col-span-6\" data-widget" in page


def test_widget_span_is_checked(admin):
    with pytest.raises(ValueError, match="span"):
        admin.widget("Bad", span=5)


def test_widget_text_is_escaped(admin, admin_client):
    @admin.widget("Unsafe")
    async def unsafe(session, user):
        return "<script>alert(1)</script>"

    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in admin_client.get("/admin/").text
