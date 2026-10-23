"""Row-level scoping (ModelAdmin.get_query) and per-record permissions (has_object_permission)."""

import csv
import io
from http import HTTPStatus

import pytest
from sqlalchemy import select

from .factories import author, post
from .server.app import MODEL_ADMINS, AuthorAdmin, PostAdmin
from .server.models import Author, Post, Status


def own(request):
    """Ids of the authors this user may see: all for admin, the one named after them for everyone else."""
    user = request.state.admin_user
    return None if user == "admin" else select(Author.id).where(Author.name == user)


class OwnPosts(PostAdmin):
    def get_query(self, request):
        q, mine = super().get_query(request), own(request)
        return q if mine is None else q.where(Post.author_id.in_(mine))

    def has_object_permission(self, user, perm, obj):
        return perm == "view" or obj.status != Status.archived     # archived posts are read-only for everyone


class OwnAuthors(AuthorAdmin):
    def get_query(self, request):
        q, mine = super().get_query(request), own(request)
        return q if mine is None else q.where(Author.id.in_(mine))


@pytest.fixture
def models():
    return (OwnPosts, OwnAuthors, *MODEL_ADMINS[2:])


@pytest.fixture
def world(db):
    me, other = author(name="editor"), author(name="Someone else")
    mine = post(title="Mine", author=me, views=1)
    theirs = post(title="Theirs", author=other, status=Status.published)
    archived = post(title="Old", author=me, status=Status.archived)
    db.add(mine, theirs, archived)
    return mine, theirs, archived


######################################################################
# get_query scopes every way in
######################################################################
def test_lists_show_only_scoped_rows(editor_client, world):
    for view in ("table", "board", "split"):
        page = editor_client.get(f"/admin/post?view={view}").text
        assert "Mine" in page and "Theirs" not in page, view
    assert '<span class="flex-1">Published</span><span class="text-xs text-faint">0</span>' in editor_client.get("/admin/post").text


def test_owner_still_sees_everything(admin_client, world):
    page = admin_client.get("/admin/post?view=table").text
    assert "Mine" in page and "Theirs" in page


def test_dashboard_counts_are_scoped(editor_client, world):
    assert "You have <b class=\"text-accent\">2</b> posts" in editor_client.get("/admin/").text


def test_palette_and_export_are_scoped(editor_client, world):
    labels = [it["label"] for g in editor_client.get("/admin/_search", params={"q": "e"}).json()["groups"] for it in g["items"]]
    assert "Mine" in labels and "Theirs" not in labels
    rows = list(csv.reader(io.StringIO(editor_client.get("/admin/post/export.csv").text)))
    assert sorted(r[1] for r in rows[1:]) == ["Mine", "Old"]


def test_lookups_are_scoped(editor_client, world):
    assert [r["label"] for r in editor_client.get("/admin/post/_lookup/author_id").json()["results"]] == ["editor"]


@pytest.mark.parametrize("method, url", [("get", "/admin/post/{pk}"), ("get", "/admin/post/{pk}/preview"),
                                         ("post", "/admin/post/{pk}"), ("post", "/admin/post/{pk}/delete"),
                                         ("post", "/admin/post/{pk}/move")])
def test_records_out_of_scope_are_not_found(editor_client, submit, world, method, url):
    _, theirs, _ = world
    url = url.format(pk=theirs.id)
    r = editor_client.get(url) if method == "get" else submit(url, {"value": "draft", "title": "x"})
    assert r.status_code == HTTPStatus.NOT_FOUND


def test_actions_skip_rows_out_of_scope(submit, db, editor_client, world):
    mine, theirs, _ = world
    r = submit("/admin/post/action", {"action": "publish", "ids": [str(mine.id), str(theirs.id)]})
    assert "Publish%3A+1+posts" in r.headers["location"]
    assert db.get(Post, mine.id).status == Status.published


def test_cannot_point_at_rows_out_of_scope(submit, db, editor_client, world):
    _, theirs, _ = world
    r = submit("/admin/post/new", {"title": "Sneaky", "author_id": str(theirs.author_id), "views": "0", "status": "draft"})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "Select a valid option." in r.text
    assert db.count(Post) == 3


######################################################################
# has_object_permission
######################################################################
def test_read_only_record(editor_client, world):
    _, _, archived = world
    page = editor_client.get(f"/admin/post/{archived.id}").text
    assert "data-save" not in page and "delete-form" not in page and 'id="actions-form"' not in page


@pytest.mark.parametrize("url, data", [("/admin/post/{pk}", {"title": "x", "author_id": "1", "views": "0", "status": "draft"}),
                                       ("/admin/post/{pk}/delete", {}), ("/admin/post/{pk}/move", {"value": "draft"}),
                                       ("/admin/post/action", {"action": "publish", "ids": "{pk}"})])
def test_writes_to_read_only_record_are_forbidden(editor_client, submit, db, world, url, data):
    _, _, archived = world
    r = submit(url.format(pk=archived.id), {k: v.format(pk=archived.id) for k, v in data.items()})
    assert r.status_code == HTTPStatus.FORBIDDEN
    assert db.get(Post, archived.id).status == Status.archived and db.get(Post, archived.id).title == "Old"


def test_object_permission_applies_to_the_owner_too(admin_client, submit, world):
    _, _, archived = world
    assert submit(f"/admin/post/{archived.id}/delete").status_code == HTTPStatus.FORBIDDEN
