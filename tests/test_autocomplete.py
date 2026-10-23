from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from .factories import author, post, tag
from .fixtures import DB, login
from .server.app import PostAdmin, create_app


def lookup(client, url, q=""):
    return client.get(url, params={"q": q}).json()["results"]


def test_lookup_lists_related_rows(admin_client, db):
    db.add(author(name="Nusrat"), author(name="Arif"))
    assert lookup(admin_client, "/admin/post/_lookup/author_id") == [{"value": "2", "label": "Arif"}, {"value": "1", "label": "Nusrat"}]


def test_lookup_searches_with_the_targets_search_fields(admin_client, db):
    db.add(author(name="Nusrat", email="n@example.com"), author(name="Arif", email="arif@example.com"))
    assert [r["label"] for r in lookup(admin_client, "/admin/post/_lookup/author_id", "arif@")] == ["Arif"]


def test_lookup_is_capped(admin_client, db):
    db.add(*[author() for _ in range(25)])
    assert len(lookup(admin_client, "/admin/post/_lookup/author_id")) == 20


def test_lookup_for_m2m_and_inline_fields(admin_client, db):
    db.add(tag(), author(name="Commenter"))
    assert lookup(admin_client, "/admin/post/_lookup/tags") == [{"value": "1", "label": "Tag #1"}]
    assert lookup(admin_client, "/admin/post/_lookup/comments.author_id") == [{"value": "1", "label": "Commenter"}]


@pytest.mark.parametrize("path", ["title", "nope", "comments.body", "comments.nope", "nope.author_id", "comments.post_id"])
def test_lookup_only_for_relations(admin_client, path):
    assert admin_client.get(f"/admin/post/_lookup/{path}").status_code == HTTPStatus.NOT_FOUND


def hide_authors(user, perm, ma):
    return ma.slug != "author"


@pytest.mark.parametrize("admin_options", [{"permission": hide_authors}])
def test_lookup_respects_target_permission(admin_client, db):
    db.add(author(name="Hidden"))
    assert admin_client.get("/admin/post/_lookup/author_id").status_code == HTTPStatus.FORBIDDEN


def test_lookup_needs_view_on_source(editor_client):
    assert editor_client.get("/admin/secret/_lookup/value").status_code == HTTPStatus.FORBIDDEN


def test_lookup_needs_sign_in(client):
    assert client.get("/admin/post/_lookup/author_id").status_code == HTTPStatus.SEE_OTHER


def test_lookup_for_unregistered_target():
    """Without a ModelAdmin for the target, lookups search its text columns and use str()."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    with TestClient(create_app(engine, models=[PostAdmin]), follow_redirects=False) as c:
        login(c)
        db = DB(c.portal.call, c.app.state.admin)
        db.add(author(name="Nusrat"), author(name="Arif"))
        assert [r["label"] for r in lookup(c, "/admin/post/_lookup/author_id", "nus")] == ["Nusrat"]
        assert [r["label"] for r in lookup(c, "/admin/post/_lookup/author_id")] == ["Nusrat", "Arif"]
        c.portal.call(engine.dispose)


def test_form_shows_current_choice(admin_client, db):
    p = db.add(post(author=author(name="Lena")))
    page = admin_client.get(f"/admin/post/{p.id}").text
    assert f'name: "author_id", url: "/admin/post/_lookup/author_id", multiple: false, selected: [{{"label": "Lena", "value": "{p.author_id}"}}]' in page


def test_malformed_fk_is_rejected(admin_client, submit):
    r = submit("/admin/post/new", {"title": "x", "author_id": "abc", "views": "0", "status": "draft"})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "Enter a valid option." in r.text
