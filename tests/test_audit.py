from http import HTTPStatus

import pytest

from fastyfold.audit import LogEntry

from .factories import author, post
from .server.models import Status


def test_add_is_logged(submit, db, admin_client):
    a = db.add(author(name="Writer"))
    submit("/admin/post/new", {"title": "Logged", "author_id": str(a.id), "views": "2", "status": "draft"})
    (entry,) = db.log()
    assert (entry.user, entry.action, entry.model, entry.object_id, entry.object_repr) == ("admin", "add", "post", "1", "Logged")
    assert entry.changed["title"] == [None, "Logged"] and entry.changed["views"] == [None, "2"]
    assert entry.at is not None


def test_change_logs_only_changed_fields(submit, db, admin_client):
    p = db.add(post(title="Before", views=1, status=Status.draft))
    submit(f"/admin/post/{p.id}", {"title": "After", "author_id": str(p.author_id), "views": "1", "status": "review"})
    (entry,) = db.log()
    assert entry.action == "change" and entry.changed == {"title": ["Before", "After"], "status": ["Draft", "Review"]}


def test_unchanged_save_logs_empty_changes(submit, db, admin_client):
    p = db.add(post(title="Same", views=1, status=Status.draft))
    submit(f"/admin/post/{p.id}", {"title": "Same", "author_id": str(p.author_id), "views": "1", "status": "draft"})
    assert db.log()[0].changed == {}


def test_delete_is_logged(submit, db, admin_client):
    p = db.add(post(title="Gone"))
    submit(f"/admin/post/{p.id}/delete")
    (entry,) = db.log()
    assert (entry.action, entry.object_id, entry.object_repr) == ("delete", str(p.id), "Gone")


def test_failed_save_is_not_logged(submit, db, admin_client):
    submit("/admin/post/new", {"title": ""})
    assert db.log() == []


def test_entries_are_by_the_signed_in_user(editor_client, submit, db):
    p = db.add(post())
    submit(f"/admin/post/{p.id}/move", {"value": "review"})
    assert db.log()[0].user == "editor"


def test_long_repr_is_truncated(submit, db, admin_client):
    p = db.add(post(title="x" * 250))
    submit(f"/admin/post/{p.id}/delete")
    assert len(db.log()[0].object_repr) == 200


def test_changed_handles_empty():
    assert LogEntry(changes="").changed == {} and LogEntry(changes='{"a": [1, 2]}').changed == {"a": [1, 2]}


######################################################################
# Log page
######################################################################
def test_log_page_empty(admin_client):
    r = admin_client.get("/admin/_log")
    assert r.status_code == HTTPStatus.OK and "No activity yet" in r.text


def test_log_page_lists_entries(admin_client, submit, db):
    p, gone = db.add(post(title="Kept"), post(title="Removed"))
    submit("/admin/post/action", {"action": "publish", "ids": str(p.id)})
    submit(f"/admin/post/{gone.id}/delete")
    page = admin_client.get("/admin/_log").text
    assert "ran “Publish” on" in page and f'href="/admin/post/{p.id}">Kept</a>' in page
    assert "deleted" in page and 'href="/admin/post/2"' not in page, "deleted rows are not linked"
    assert page.index("Removed") < page.index("Kept"), "newest first"


def test_log_page_paginates(admin_client, submit, db):
    rows = db.add(*[post() for _ in range(55)])
    for p in rows:
        submit(f"/admin/post/{p.id}/move", {"value": "review"})
    first = admin_client.get("/admin/_log").text
    second = admin_client.get("/admin/_log?page=2").text
    assert "Older" in first and "Newer" not in first
    assert "Newer" in second and "Older" not in second
    assert first.count("changed") >= 50 and second.count("<li class=\"flex gap-3\">") == 5


def test_record_history(admin_client, submit, db):
    p = db.add(post(title="Tracked"))
    submit(f"/admin/post/{p.id}/move", {"value": "review"})
    form = admin_client.get(f"/admin/post/{p.id}").text
    assert "last changed by <b class=\"text-fg\">admin</b>" in form and "status: review" in form
    assert "status: review" in admin_client.get(f"/admin/post/{p.id}/preview").text


def test_history_is_per_record(admin_client, submit, db):
    a, b = db.add(post(title="A"), post(title="B"))
    submit(f"/admin/post/{a.id}/move", {"value": "review"})
    assert "status: review" not in admin_client.get(f"/admin/post/{b.id}").text


######################################################################
# Who may see what, filters and the diff
######################################################################
def test_log_hides_models_the_user_cannot_view(client, db):
    from .factories import secret
    from .fixtures import login

    login(client, "admin")
    s = db.add(secret(value="classified"))
    token = client.app.state.admin.signer.csrf(client.cookies.get("ff_session"))
    client.post(f"/admin/secret/{s.id}", data={"_csrf": token, "value": "changed"})
    assert "changed" in client.get("/admin/_log").text
    login(client, "editor")
    assert "classified" not in client.get("/admin/_log").text and "changed" not in client.get("/admin/_log").text
    assert "changed" not in client.get("/admin/").text.split("Team activity")[1]


@pytest.fixture
def history(admin_client, submit, db):
    a, b = db.add(post(title="Alpha"), post(title="Beta"))
    submit(f"/admin/post/{a.id}/move", {"value": "review"})
    submit("/admin/post/action", {"action": "publish", "ids": str(b.id)})
    au = db.add(author(name="Writer"))
    submit(f"/admin/author/{au.id}", {"name": "Writer 2"})
    return a, b


def entries(page):
    return [m for m in ("Alpha", "Beta", "Writer 2") if f">{m}</a>" in page]


@pytest.mark.parametrize("query, expected", [
    ("", ["Alpha", "Beta", "Writer 2"]),
    ("model=author", ["Writer 2"]),
    ("model=post", ["Alpha", "Beta"]),
    ("action=publish", ["Beta"]),
    ("action=change&model=post", ["Alpha"]),
    ("who=admin&days=today", ["Alpha", "Beta", "Writer 2"]),
    ("who=nobody", []),
    ("model=secret", []),
    ("model=nope", ["Alpha", "Beta", "Writer 2"]),
])
def test_log_filters(admin_client, history, query, expected):
    assert entries(admin_client.get(f"/admin/_log?{query}").text) == expected


def test_log_filter_menus(admin_client, history):
    page = admin_client.get("/admin/_log?model=post").text
    assert '<option value="admin" >admin</option>' in page
    assert '<option value="post" selected>Posts</option>' in page and '<option value="author" >Authors</option>' in page
    assert '<option value="publish" >publish</option>' in page and "Clear filters" in page


def test_log_shows_old_and_new(admin_client, history):
    page = admin_client.get("/admin/_log").text
    assert '<del class="text-faint">draft</del>' in page and '<ins class="font-semibold no-underline">review</ins>' in page
    assert '<del class="text-faint">Writer</del>' in page


def test_log_pager_keeps_filters(admin_client, submit, db):
    rows = db.add(*[post() for _ in range(55)])
    for p in rows:
        submit(f"/admin/post/{p.id}/move", {"value": "review"})
    page = admin_client.get("/admin/_log?model=post&action=change").text
    assert 'href="/admin/_log?page=2&amp;model=post&amp;action=change"' in page
