import re
from http import HTTPStatus

import pytest

from fastyfold.security import SESSION_COOKIE

from .factories import post
from .fixtures import login
from .server.models import Post, Status

WRITES = [
    ("/admin/logout", {}),
    ("/admin/post/new", {"title": "New", "author_id": "1", "views": "0", "status": "draft"}),
    ("/admin/post/{pk}", {"title": "Changed", "author_id": "1", "views": "0", "status": "draft"}),
    ("/admin/post/{pk}/delete", {}),
    ("/admin/post/{pk}/move", {"value": "published"}),
    ("/admin/post/action", {"action": "publish", "ids": "{pk}"}),
]


@pytest.fixture
def row(admin_client, db):
    return db.add(post(title="Original"))


def assert_untouched(db, row):
    saved = db.get(Post, row.id)
    assert saved is not None and saved.title == "Original" and saved.status == Status.draft
    assert db.count(Post) == 1


@pytest.mark.parametrize("url, data", WRITES)
def test_write_without_token_is_rejected(admin_client, db, row, url, data):
    data = {k: v.format(pk=row.id) for k, v in data.items()}
    r = admin_client.post(url.format(pk=row.id), data=data)
    assert r.status_code == HTTPStatus.FORBIDDEN
    assert_untouched(db, row)


@pytest.mark.parametrize("url, data", WRITES)
def test_write_with_wrong_token_is_rejected(admin_client, db, row, url, data):
    data = {k: v.format(pk=row.id) for k, v in data.items()}
    r = admin_client.post(url.format(pk=row.id), data={**data, "_csrf": "0" * 64})
    assert r.status_code == HTTPStatus.FORBIDDEN
    assert_untouched(db, row)


def test_token_in_header_is_accepted(admin_client, csrf, db, row):
    r = admin_client.post(f"/admin/post/{row.id}/move", data={"value": "published"}, headers={"X-CSRF-Token": csrf()})
    assert r.status_code == HTTPStatus.NO_CONTENT


def test_page_carries_token_in_meta_and_forms(admin_client, csrf):
    page = admin_client.get("/admin/post?view=table").text
    assert re.search(r'name="csrf-token" content="([0-9a-f]+)"', page).group(1) == csrf()
    assert f'name="_csrf" value="{csrf()}"' in page


def test_token_from_previous_session_is_rejected(client, db):
    login(client)
    row = db.add(post(title="Original"))
    old = client.app.state.admin.signer.csrf(client.cookies.get(SESSION_COOKIE))
    login(client)
    r = client.post(f"/admin/post/{row.id}/delete", data={"_csrf": old})
    assert r.status_code == HTTPStatus.FORBIDDEN
    assert_untouched(db, row)


def test_login_does_not_need_token(client):
    assert login(client).status_code == HTTPStatus.SEE_OTHER
