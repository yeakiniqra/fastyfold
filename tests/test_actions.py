from http import HTTPStatus
from urllib.parse import parse_qs, urlsplit

import pytest

from .factories import post
from .server.models import Post, Status


@pytest.fixture
def rows(admin_client, db):
    return db.add(post(title="One"), post(title="Two"), post(title="Three"))


def msg_of(location):
    return parse_qs(urlsplit(location).query)["msg"][0]


######################################################################
# Custom @action
######################################################################
def test_action_runs_on_selected_rows(submit, db, rows):
    one, two, three = rows
    r = submit("/admin/post/action", {"action": "publish", "ids": [str(one.id), str(two.id)]})
    assert r.status_code == HTTPStatus.SEE_OTHER and msg_of(r.headers["location"]) == "Publish: 2 posts"
    assert db.get(Post, one.id).status == Status.published
    assert db.get(Post, two.id).status == Status.published
    assert db.get(Post, three.id).status == Status.draft


def test_action_is_logged_per_row(submit, db, rows):
    submit("/admin/post/action", {"action": "publish", "ids": [str(rows[0].id), str(rows[1].id)]})
    entries = [(e.action, e.object_id) for e in db.log()]
    assert sorted(entries) == [("publish", "1"), ("publish", "2")]


def test_action_buttons_render(admin_client, rows):
    page = admin_client.get("/admin/post?view=table").text
    assert 'name="action" value="publish"' in page
    assert """onclick='return confirm("Archive the selected posts?")'""" in page


def test_unknown_action(submit, rows):
    assert submit("/admin/post/action", {"action": "explode", "ids": "1"}).status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.parametrize("name", ["save", "delete_model", "__init__", "has_permission"])
def test_only_decorated_methods_run(submit, rows, name):
    assert submit("/admin/post/action", {"action": name, "ids": "1"}).status_code == HTTPStatus.BAD_REQUEST


def test_no_rows_selected(submit, db, rows):
    r = submit("/admin/post/action", {"action": "publish"})
    assert r.status_code == HTTPStatus.SEE_OTHER and msg_of(r.headers["location"]) == "Select at least one row first"
    assert db.get(Post, 1).status == Status.draft


def test_unknown_ids_are_skipped(submit, rows):
    r = submit("/admin/post/action", {"action": "publish", "ids": "999"})
    assert msg_of(r.headers["location"]) == "Publish: 0 posts"


def test_malformed_ids(submit, db, rows):
    r = submit("/admin/post/action", {"action": "publish", "ids": ["1", "abc"]})
    assert r.status_code == HTTPStatus.BAD_REQUEST
    assert db.get(Post, 1).status == Status.draft


######################################################################
# Bulk delete
######################################################################
def test_bulk_delete(submit, db, rows):
    r = submit("/admin/post/action", {"action": "delete", "ids": ["1", "3"]})
    assert msg_of(r.headers["location"]) == "Deleted: 2 posts"
    assert db.count(Post) == 1 and db.get(Post, 2) is not None
    assert sorted((e.action, e.object_repr) for e in db.log()) == [("delete", "One"), ("delete", "Three")]


######################################################################
# Where we go afterwards
######################################################################
@pytest.mark.parametrize("next_url, target", [
    ("/admin/post?view=split", "/admin/post?view=split&msg="),
    ("/admin/post/2", "/admin/post/2?msg="),
    ("", "/admin/post?msg="),
    ("https://evil.example", "/admin/post?msg="),
    ("//evil.example", "/admin/post?msg="),
    ("/elsewhere", "/admin/post?msg="),
])
def test_next(submit, rows, next_url, target):
    r = submit("/admin/post/action", {"action": "publish", "ids": "1", "next": next_url})
    assert r.headers["location"].startswith(target)


######################################################################
# Actions on the edit page
######################################################################
def test_record_actions_on_form(admin_client, rows):
    page = admin_client.get("/admin/post/1").text
    assert 'id="actions-form"' in page and 'name="ids" value="1"' in page and 'name="next" value="/admin/post/1"' in page
    assert 'form="actions-form" name="action" value="publish" data-action-key="u"' in page
    assert """onclick='return confirm("Archive the selected posts?")'""" in page


def test_record_action_returns_to_the_form(submit, db, rows):
    r = submit("/admin/post/action", {"action": "publish", "ids": "1", "next": "/admin/post/1"})
    assert r.headers["location"] == "/admin/post/1?msg=Publish%3A+1+posts"
    assert db.get(Post, 1).status == Status.published


def test_record_actions_follow_permissions(editor_client, db):
    db.add(post())
    page = editor_client.get("/admin/post/1").text
    assert 'value="publish"' in page and 'value="archive"' not in page


def test_new_form_has_no_record_actions(admin_client):
    assert 'id="actions-form"' not in admin_client.get("/admin/post/new").text
