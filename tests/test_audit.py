from http import HTTPStatus

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
