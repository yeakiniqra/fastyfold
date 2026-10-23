import csv
import io
from datetime import datetime
from http import HTTPStatus

import pytest

from .factories import author, post
from .server.models import Status


def rows_of(r):
    return list(csv.reader(io.StringIO(r.text)))


def test_export_all(admin_client, db):
    db.add(post(title="Alpha", views=5, published_at=datetime(2026, 1, 2, 3, 4), author=author(name="Nusrat")))
    r = admin_client.get("/admin/post/export.csv")
    assert r.status_code == HTTPStatus.OK
    assert r.headers["content-type"].startswith("text/csv")
    assert r.headers["content-disposition"] == 'attachment; filename="post.csv"'
    header, row = rows_of(r)
    assert header == ["ID", "Title", "Author", "Status", "Views", "Published at", "Reach"]
    assert row == ["1", "Alpha", "Nusrat", "Draft", "5", "2026-01-02T03:04:00", "15 people"]


def test_export_follows_search_filters_and_sort(admin_client, db):
    db.add(post(title="Alpha live", status=Status.published), post(title="Beta live", status=Status.published),
           post(title="Alpha draft"))
    r = admin_client.get("/admin/post/export.csv?q=alpha&f_status=published")
    assert [row[1] for row in rows_of(r)[1:]] == ["Alpha live"]
    r = admin_client.get("/admin/post/export.csv?sort=title")
    assert [row[1] for row in rows_of(r)[1:]] == ["Alpha draft", "Alpha live", "Beta live"]


@pytest.mark.parametrize("title, cell", [("=HYPERLINK(\"x\")", "'=HYPERLINK(\"x\")"), ("+1", "'+1"), ("-1", "'-1"),
                                         ("@SUM(A1)", "'@SUM(A1)"), ("plain", "plain")])
def test_export_neutralises_formulas(admin_client, db, title, cell):
    db.add(post(title=title))
    assert rows_of(admin_client.get("/admin/post/export.csv"))[1][1] == cell


def test_export_keeps_negative_numbers(admin_client, db):
    db.add(post(views=-5))
    assert rows_of(admin_client.get("/admin/post/export.csv"))[1][4] == "-5"


def test_export_empty_values(admin_client, db):
    db.add(author(name="Plain", email=None))
    header, row = rows_of(admin_client.get("/admin/author/export.csv"))
    assert row[header.index("Email")] == "" and row[header.index("Active")] == "True"


def test_export_selected_rows(submit, admin_client, db):
    db.add(post(title="One"), post(title="Two"), post(title="Three"))
    r = submit("/admin/post/action", {"action": "export", "ids": ["1", "3"]})
    assert r.status_code == HTTPStatus.OK and r.headers["content-type"].startswith("text/csv")
    assert [row[1] for row in rows_of(r)[1:]] == ["One", "Three"]


def test_export_buttons(admin_client, db):
    db.add(post())
    page = admin_client.get("/admin/post?view=table&q=a&f_status=draft").text
    assert 'href="/admin/post/export.csv?q=a&amp;f_status=draft"' in page
    assert 'name="action" value="export"' in page


def test_export_in_palette(admin_client):
    groups = {g["title"]: g["items"] for g in admin_client.get("/admin/_search", params={"q": "export po"}).json()["groups"]}
    assert groups["Actions"][0] == {"label": "Export posts", "sub": "CSV", "url": "/admin/post/export.csv", "icon": "download"}


def no_export(user, perm, ma):
    return perm != "export"


@pytest.mark.parametrize("admin_options", [{"permission": no_export}])
def test_export_permission(admin_client, submit, db):
    db.add(post())
    assert admin_client.get("/admin/post/export.csv").status_code == HTTPStatus.FORBIDDEN
    assert submit("/admin/post/action", {"action": "export", "ids": "1"}).status_code == HTTPStatus.FORBIDDEN
    page = admin_client.get("/admin/post?view=table").text
    assert "export.csv" not in page and 'value="export"' not in page


def test_export_needs_sign_in(client):
    assert client.get("/admin/post/export.csv").status_code == HTTPStatus.SEE_OTHER
