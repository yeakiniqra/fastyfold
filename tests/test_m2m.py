from http import HTTPStatus

import pytest

from .factories import post, tag
from .server.models import Post


@pytest.fixture
def tags(admin_client, db):
    return db.add(tag(), tag(), tag())


def post_data(p, *tag_ids, **kw):
    return {"title": p.title, "author_id": str(p.author_id), "views": "0", "status": "draft", "tags": [str(t) for t in tag_ids], **kw}


def tag_ids(db, pk):
    return sorted(t.id for t in db.get(Post, pk, "tags").tags)


def test_form_shows_selected(admin_client, db, tags):
    p = db.add(post(tags=[tags[0], tags[2]]))
    page = admin_client.get(f"/admin/post/{p.id}").text
    assert ('name: "tags", url: "/admin/post/_lookup/tags", multiple: true, '
            'selected: [{"label": "Tag #1", "value": "1"}, {"label": "Tag #3", "value": "3"}]') in page


def test_set_change_and_clear(submit, db, tags):
    p = db.add(post())
    submit(f"/admin/post/{p.id}", post_data(p, 1, 2))
    assert tag_ids(db, p.id) == [1, 2]
    submit(f"/admin/post/{p.id}", post_data(p, 2, 3))
    assert tag_ids(db, p.id) == [2, 3]
    submit(f"/admin/post/{p.id}", post_data(p))
    assert tag_ids(db, p.id) == []


def test_create_with_tags(submit, db, tags):
    a = post().author
    db.add(a)
    r = submit("/admin/post/new", {"title": "Tagged", "author_id": str(a.id), "views": "0", "status": "draft", "tags": ["1", "3"]})
    assert r.status_code == HTTPStatus.SEE_OTHER
    assert tag_ids(db, 1) == [1, 3]


@pytest.mark.parametrize("bad", [["abc"], ["99"], ["1", "99"]])
def test_invalid_choices(submit, db, tags, bad):
    p = db.add(post(tags=[tags[0]]))
    r = submit(f"/admin/post/{p.id}", {**post_data(p), "tags": bad})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "Select a valid option." in r.text
    assert tag_ids(db, p.id) == [1]


def test_changes_are_logged(submit, db, tags):
    p = db.add(post(tags=[tags[0]]))
    submit(f"/admin/post/{p.id}", post_data(p, 1, 2))
    assert db.log()[0].changed["tags"] == ["Tag #1", "Tag #1, Tag #2"]


def test_display(admin, admin_client, db, tags):
    p = db.add(post(tags=[tags[0], tags[1]]))
    assert admin.models["post"].display(db.get(Post, p.id, "tags"), "tags") == "Tag #1, Tag #2"
    assert admin.models["post"].display(post(tags=[]), "tags") == "—"


def test_m2m_is_not_sortable(admin_client, db, tags):
    db.add(post(tags=[tags[0]]))
    assert admin_client.get("/admin/post?view=table&sort=tags").status_code == HTTPStatus.OK
