from http import HTTPStatus

import pytest

from .factories import author, post
from .server.models import Post, Status


def test_board_columns(admin_client, db):
    db.add(post())
    page = admin_client.get("/admin/post?view=board").text
    for value in ("draft", "review", "published", "archived"):
        assert f'data-column="{value}" data-move-url="/admin/post"' in page
    assert 'draggable="true"' in page


def test_move(submit, db, admin_client):
    p = db.add(post(status=Status.draft))
    r = submit(f"/admin/post/{p.id}/move", {"value": "published"})
    assert r.status_code == HTTPStatus.NO_CONTENT
    assert db.get(Post, p.id).status == Status.published


def test_move_is_logged(submit, db, admin_client):
    p = db.add(post(title="Moved", status=Status.draft))
    submit(f"/admin/post/{p.id}/move", {"value": "review"})
    (entry,) = db.log()
    assert (entry.action, entry.object_id, entry.object_repr) == ("change", str(p.id), "Moved")
    assert entry.changed == {"status": ["draft", "review"]}


@pytest.mark.parametrize("data", [{"value": "deleted"}, {"value": ""}, {}])
def test_move_rejects_bad_value(submit, db, admin_client, data):
    p = db.add(post(status=Status.draft))
    assert submit(f"/admin/post/{p.id}/move", data).status_code == HTTPStatus.BAD_REQUEST
    assert db.get(Post, p.id).status == Status.draft


def test_move_missing_record(submit, admin_client):
    assert submit("/admin/post/99/move", {"value": "review"}).status_code == HTTPStatus.NOT_FOUND


def test_move_needs_board(submit, db, admin_client):
    a = db.add(author())
    assert submit(f"/admin/author/{a.id}/move", {"value": "x"}).status_code == HTTPStatus.BAD_REQUEST
