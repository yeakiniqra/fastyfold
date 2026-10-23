from http import HTTPStatus

import pytest

from .factories import author, comment, post
from .server.models import Comment, Post


@pytest.fixture
def parent(admin_client, db):
    """A post with two comments."""
    return db.add(post(title="Parent", comments=[comment(body="First", likes=1), comment(body="Second", likes=2)]))


def post_data(p, **kw):
    return {"title": p.title, "author_id": str(p.author_id), "views": "0", "status": "draft", **kw}


def bodies(db, pk):
    return sorted((c.body, c.likes, c.approved) for c in db.get(Post, pk, "comments").comments)


def test_form_lists_children(admin_client, parent):
    page = admin_client.get(f"/admin/post/{parent.id}").text
    assert 'id="inline-comments"' in page and "Comments" in page
    assert 'name="comments-0-body" value="First"' in page and 'name="comments-1-body" value="Second"' in page
    assert 'name="comments-0-id" value="1"' in page and 'name="comments-0-_delete"' in page
    assert "data-add-row" in page and "x-data=\"{ added: [], next: 2 }\"" in page
    assert 'name="comments-0-post_id"' not in page, "the link to the parent is implied"


def test_new_form_has_empty_inline(admin_client):
    page = admin_client.get("/admin/post/new").text
    assert 'id="inline-comments"' in page and "x-data=\"{ added: [], next: 0 }\"" in page


def test_create_with_children(submit, db, admin_client):
    a = db.add(author())
    r = submit("/admin/post/new", {"title": "New", "author_id": str(a.id), "views": "0", "status": "draft",
                                   "comments-0-body": "Hi", "comments-0-likes": "3", "comments-0-approved": "on",
                                   "comments-1-body": "", "comments-1-likes": ""})
    assert r.status_code == HTTPStatus.SEE_OTHER
    assert bodies(db, 1) == [("Hi", 3, True)], "the blank row is ignored"


def test_add_change_and_remove(submit, db, parent):
    r = submit(f"/admin/post/{parent.id}", post_data(parent, **{
        "comments-0-id": "1", "comments-0-body": "First edited", "comments-0-likes": "1",
        "comments-1-id": "2", "comments-1-body": "Second", "comments-1-likes": "2", "comments-1-_delete": "1",
        "comments-7-body": "Third", "comments-7-likes": "0",
    }))
    assert r.status_code == HTTPStatus.SEE_OTHER
    assert bodies(db, parent.id) == [("First edited", 1, False), ("Third", 0, False)]
    assert db.count(Comment) == 2
    assert db.log()[0].changed["comments"] == [None, "1 added, 1 changed, 1 removed"]


def test_untouched_children_are_kept(submit, db, parent):
    submit(f"/admin/post/{parent.id}", post_data(parent, **{
        "comments-0-id": "1", "comments-0-body": "First", "comments-0-likes": "1",
        "comments-1-id": "2", "comments-1-body": "Second", "comments-1-likes": "2"}))
    assert bodies(db, parent.id) == [("First", 1, False), ("Second", 2, False)]
    assert "comments" not in db.log()[0].changed


def test_inline_errors_rerender(submit, db, parent):
    r = submit(f"/admin/post/{parent.id}", post_data(parent, **{
        "comments-0-id": "1", "comments-0-body": "", "comments-0-likes": "lots",
        "comments-4-body": "New one", "comments-4-likes": "1"}))
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "This field is required." in r.text and "Enter a valid whole number." in r.text
    assert 'name="comments-0-likes" value="lots"' in r.text and 'name="comments-4-body" value="New one"' in r.text
    assert "x-data=\"{ added: [], next: 5 }\"" in r.text, "browser rows continue after the highest index"
    assert bodies(db, parent.id) == [("First", 1, False), ("Second", 2, False)]


def test_cannot_touch_another_records_children(submit, db, parent):
    other = db.add(post(title="Other", comments=[comment(body="Not yours")]))
    theirs = db.get(Post, other.id, "comments").comments[0]
    submit(f"/admin/post/{parent.id}", post_data(parent, **{
        "comments-0-id": str(theirs.id), "comments-0-body": "Hijacked", "comments-0-likes": "0",
        "comments-1-id": str(theirs.id), "comments-1-body": "x", "comments-1-likes": "0", "comments-1-_delete": "1"}))
    assert db.get(Comment, theirs.id).body == "Not yours"


def test_inline_fk_uses_autocomplete(admin_client, db):
    a = db.add(author(name="Commenter"))
    p = db.add(post(comments=[comment(author_id=a.id)]))
    page = admin_client.get(f"/admin/post/{p.id}").text
    assert ('name: "comments-0-author_id", url: "/admin/post/_lookup/comments.author_id", multiple: false, '
            f'selected: [{{"label": "Commenter", "value": "{a.id}"}}]') in page


def test_deleting_parent_deletes_children(submit, db, parent):
    submit(f"/admin/post/{parent.id}/delete")
    assert db.count(Comment) == 0


def test_bulk_delete_with_children(submit, db, parent):
    submit("/admin/post/action", {"action": "delete", "ids": str(parent.id)})
    assert db.count(Post) == 0 and db.count(Comment) == 0


def read_only(user, perm, ma):
    return perm == "view"


@pytest.mark.parametrize("admin_options", [{"permission": read_only}])
def test_inlines_are_read_only_without_change_permission(admin_client, db):
    p = db.add(post(comments=[comment(body="Locked")]))
    page = admin_client.get(f"/admin/post/{p.id}").text
    assert 'name="comments-0-body" value="Locked" aria-label="Body" disabled' in page
    assert "data-add-row" not in page and "comments-0-_delete" not in page
