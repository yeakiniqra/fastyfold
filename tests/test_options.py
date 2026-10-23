"""ModelAdmin options: column_labels, exclude, save_as, per_page_options, select-all-matching."""

import re

import pytest

from .factories import author, post
from .server.app import MODEL_ADMINS, AuthorAdmin, PostAdmin
from .server.models import Author, Post, Status


class LabelledPosts(PostAdmin):
    column_labels = {"views": "Readers", "reach": "Audience", "author.name": "Writer"}
    list_display = ["id", "title", "author.name", "views", "reach"]
    save_as = True
    per_page_options = [2, 25]
    select_all_limit = 5


class SlimAuthors(AuthorAdmin):
    exclude = ["email", "bio"]


@pytest.fixture
def models():
    return (LabelledPosts, SlimAuthors, *MODEL_ADMINS[2:])


def test_column_labels(admin_client, db):
    db.add(post())
    page = admin_client.get("/admin/post?view=table").text
    headers = re.findall(r"<th[^>]*>(.*?)</th>", page, re.S)
    text = [re.sub(r"<[^>]+>|\s+", " ", h).strip() for h in headers]
    assert {"Readers", "Audience", "Writer"} <= {t.split(" ")[0] for t in text}
    assert "Readers" in admin_client.get("/admin/post/1").text, "form labels follow too"


def test_exclude(admin_client, submit, db):
    db.add(author(name="Kept", email="kept@example.com", bio="Old bio"))
    form = admin_client.get("/admin/author/1").text
    assert 'name="email"' not in form and 'name="bio"' not in form and 'name="name"' in form
    submit("/admin/author/1", {"name": "Renamed", "email": "hacked@example.com", "bio": "x"})
    a = db.get(Author, 1)
    assert (a.name, a.email, a.bio) == ("Renamed", "kept@example.com", "Old bio")


def test_save_as_new(admin_client, submit, db):
    p = db.add(post(title="Original"))
    page = admin_client.get(f"/admin/post/{p.id}").text
    assert 'formaction="/admin/post/new"' in page and "data-save-as" in page
    r = submit("/admin/post/new", {"title": "Copy", "author_id": str(p.author_id), "views": "0", "status": "draft", "_continue": "1"})
    assert r.headers["location"].startswith("/admin/post/2?")
    assert db.get(Post, 1).title == "Original" and db.get(Post, 2).title == "Copy"


def test_save_as_is_off_by_default(admin_client, db):
    db.add(author())
    assert "data-save-as" not in admin_client.get("/admin/author/1").text


def test_per_page_options(admin_client, db):
    db.add(*[post(title=f"Row {i}") for i in range(3)])
    page = admin_client.get("/admin/post?view=table&per_page=2").text
    assert "Row 2" in page and "Row 1" in page and "Row 0" not in page and "of <b class=\"text-fg\">2</b>" in page
    assert 'href="/admin/post?view=table&amp;page=2&amp;per_page=2"' in page, "paging keeps the chosen size"
    assert "Row 0" in admin_client.get("/admin/post?view=table&per_page=7").text, "only listed sizes are allowed"


######################################################################
# Select all matching
######################################################################
def test_select_all_button_appears_when_there_is_more(admin_client, db):
    db.add(*[post() for _ in range(3)])
    assert "Select all 3 matching" in admin_client.get("/admin/post?view=table&per_page=2").text
    assert "data-select-all" not in admin_client.get("/admin/post?view=table").text


def test_select_all_matching_runs_on_every_match(submit, db, admin_client):
    db.add(*[post(title=f"Match {i}") for i in range(4)], post(title="Other"))
    r = submit("/admin/post/action", {"action": "publish", "select_all": "1", "query": "view=table&q=match&per_page=2", "ids": "1"})
    assert "Publish%3A+4+posts" in r.headers["location"]
    assert [db.get(Post, i).status for i in range(1, 6)] == [Status.published] * 4 + [Status.draft]


def test_select_all_matching_respects_the_limit(submit, db, admin_client):
    db.add(*[post() for _ in range(6)])
    r = submit("/admin/post/action", {"action": "publish", "select_all": "1", "query": ""})
    assert "more+than+5" in r.headers["location"]
    assert all(db.get(Post, i).status == Status.draft for i in range(1, 7))
