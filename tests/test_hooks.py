"""Lifecycle hooks around writes, and database constraint errors shown as messages instead of 500s."""

from http import HTTPStatus
from pathlib import Path

import pytest

from .factories import PNG, author, post
from .server.app import MODEL_ADMINS, PostAdmin
from .server.models import Author

calls: list = []


class HookedPosts(PostAdmin):
    async def after_save(self, obj, is_new):
        calls.append(("saved", obj.title, is_new))

    def after_delete(self, obj):            # sync hooks work too
        calls.append(("deleted", obj.title))


@pytest.fixture
def models():
    calls.clear()
    return (HookedPosts, *MODEL_ADMINS[1:])


def post_data(**kw):
    return {"title": "Hooked", "author_id": "1", "views": "0", "status": "draft", **kw}


######################################################################
# after_save / after_delete
######################################################################
def test_after_save_runs_after_commit(admin_client, submit, db):
    db.add(author())
    submit("/admin/post/new", post_data())
    submit("/admin/post/1", post_data(title="Renamed"))
    assert calls == [("saved", "Hooked", True), ("saved", "Renamed", False)]


def test_after_save_skipped_when_saving_fails(admin_client, submit, db):
    db.add(author())
    submit("/admin/post/new", post_data(views="lots"))
    assert calls == []


def test_after_delete(admin_client, submit, db):
    db.add(post(title="One"), post(title="Two"))
    submit("/admin/post/1/delete")
    submit("/admin/post/action", {"action": "delete", "ids": "2"})
    assert calls == [("deleted", "One"), ("deleted", "Two")]


######################################################################
# Constraint errors
######################################################################
def test_unique_violation_keeps_the_form(admin_client, submit, db):
    db.add(author(name="First", email="same@example.com"))
    r = submit("/admin/author/new", {"name": "Second", "email": "same@example.com"})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "Couldn&#39;t save:" in r.text and "email" in r.text.lower()
    assert 'value="Second"' in r.text
    assert db.count(Author) == 1 and db.log() == []


def test_unique_violation_on_edit(admin_client, submit, db):
    db.add(author(name="First", email="taken@example.com"), author(name="Second", email="free@example.com"))
    r = submit("/admin/author/2", {"name": "Second", "email": "taken@example.com"})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "Couldn&#39;t save:" in r.text
    assert db.get(Author, 2).email == "free@example.com"


def test_failed_save_removes_the_new_upload(admin_client, submit, db, admin):
    db.add(author(name="First", email="taken@example.com"))
    r = submit("/admin/author/new", {"name": "Second", "email": "taken@example.com"}, files={"photo": ("me.png", PNG, "image/png")})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert [p for p in Path(admin.storage.root).rglob("*") if p.is_file()] == []


def test_delete_blocked_by_references(admin_client, submit, db):
    db.add(post(author=author(name="Referenced")))
    r = submit("/admin/author/1/delete")
    assert r.status_code == HTTPStatus.SEE_OTHER and r.headers["location"].startswith("/admin/author/1?msg=Couldn")
    assert db.get(Author, 1) is not None and db.log() == []


def test_bulk_delete_blocked_by_references(admin_client, submit, db):
    db.add(post(author=author(name="Referenced")), author(name="Free"))
    r = submit("/admin/author/action", {"action": "delete", "ids": ["1", "2"]})
    assert "Nothing+changed" in r.headers["location"]
    assert db.count(Author) == 2, "all or nothing"
