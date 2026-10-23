"""The JSON API (Admin(api=True)): typed CRUD that shares permissions, scoping, validation and audit with the HTML views."""

from http import HTTPStatus

import pytest
from fastapi import Header, HTTPException
from fastapi.testclient import TestClient

from .factories import author, post, tag
from .fixtures import login
from .server.models import Author, Post, Status


@pytest.fixture
def admin_options():
    return {"api": True}


@pytest.fixture
def api(admin_client, csrf):
    """The signed-in test client, sending the CSRF header like a browser script would."""
    admin_client.headers["X-CSRF-Token"] = csrf()
    return admin_client


######################################################################
# Reading
######################################################################
def test_needs_sign_in(client):
    r = client.get("/admin/api/post")
    assert r.status_code == HTTPStatus.UNAUTHORIZED and "Sign in" in r.json()["detail"]


def test_list(api, db):
    db.add(post(title="Alpha", views=3, author=author(name="Nusrat")), post(title="Beta", status=Status.published))
    body = api.get("/admin/api/post").json()
    assert body["total"] == 2 and body["page"] == 1 and body["per_page"] == 25
    beta, alpha = body["items"]
    assert alpha == {"id": 1, "title": "Alpha", "author_id": 1, "status": "draft", "views": 3, "rating": None, "meta": None,
                     "published_at": None, "summary": None, "tags": []}
    assert beta["status"] == "published"


def test_list_search_filter_sort_page(api, db):
    db.add(*[post(title=f"Match {i}", views=i) for i in range(5)], post(title="Other", views=99))
    body = api.get("/admin/api/post", params={"q": "match", "f_views": "1..3", "sort": "views", "per_page": 2, "page": 2}).json()
    assert body["total"] == 3 and [r["title"] for r in body["items"]] == ["Match 3"]


@pytest.mark.parametrize("params", [{"per_page": 0}, {"per_page": 101}, {"page": 0}])
def test_list_bounds(api, params):
    assert api.get("/admin/api/post", params=params).status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_get(api, db):
    db.add(post(title="One", tags=[tag()]))
    assert api.get("/admin/api/post/1").json()["tags"] == [1]
    assert api.get("/admin/api/post/99").status_code == HTTPStatus.NOT_FOUND
    assert api.get("/admin/api/post/abc").status_code == HTTPStatus.NOT_FOUND


def test_display_only_types_are_left_out(api, db):
    from datetime import timedelta

    from .server.models import Ticket
    t = db.add(Ticket(code="VIP", window=timedelta(hours=1), blob=b"\x00"))
    body = api.get(f"/admin/api/ticket/{t.id}").json()
    assert body["id"] == str(t.id) and "window" not in body and "blob" not in body


######################################################################
# Writing
######################################################################
def test_create(api, db):
    db.add(author(), tag(), tag())
    r = api.post("/admin/api/post", json={"title": "Via API", "author_id": 1, "tags": [1, 2]})
    assert r.status_code == HTTPStatus.CREATED
    body = r.json()
    assert body["id"] == 1 and body["status"] == "draft" and body["views"] == 0 and body["tags"] == [1, 2]
    (entry,) = db.log()
    assert (entry.user, entry.action, entry.object_repr) == ("admin", "add", "Via API")


def test_server_defaults_come_back(api):
    body = api.post("/admin/api/author", json={"name": "Fresh"}).json()
    assert body["joined"] is not None and body["active"] is True


@pytest.mark.parametrize("payload, field, message", [
    ({"author_id": 1}, "title", "Field required"),
    ({"title": "x", "author_id": 1, "status": "deleted"}, "status", "Input should be"),
    ({"title": "x", "author_id": 99}, "author_id", "Select a valid option."),
    ({"title": "x", "author_id": 1, "tags": [99]}, "tags", "Select a valid option."),
    ({"title": "Buy spam", "author_id": 1}, "title", "No spam"),
    ({"title": "x", "author_id": 1, "views": "many"}, "views", "valid integer"),
])
def test_validation(api, db, payload, field, message):
    db.add(author())
    r = api.post("/admin/api/post", json=payload)
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    errors = r.json()["detail"]
    assert any(e["loc"][-1] == field and message in e["msg"] for e in errors), errors
    assert db.count(Post) == 0


def test_update_is_partial(api, db):
    db.add(post(title="Before", views=5))
    r = api.patch("/admin/api/post/1", json={"title": "After"})
    assert r.status_code == HTTPStatus.OK and r.json()["title"] == "After" and r.json()["views"] == 5
    assert db.log()[0].changed == {"title": ["Before", "After"]}


def test_update_can_clear_optional_fields(api, db):
    db.add(post(summary="Something"))
    assert api.patch("/admin/api/post/1", json={"summary": None}).json()["summary"] is None
    assert api.patch("/admin/api/post/1", json={"title": None}).status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_delete(api, db):
    db.add(post(title="Doomed"))
    assert api.delete("/admin/api/post/1").status_code == HTTPStatus.NO_CONTENT
    assert db.get(Post, 1) is None and db.log()[0].action == "delete"
    assert api.delete("/admin/api/post/1").status_code == HTTPStatus.NOT_FOUND


def test_conflicts_are_409(api, db):
    db.add(post(author=author(email="taken@example.com")))
    assert api.post("/admin/api/author", json={"name": "Dup", "email": "taken@example.com"}).status_code == HTTPStatus.CONFLICT
    assert api.delete("/admin/api/author/1").status_code == HTTPStatus.CONFLICT, "posts still point at this author"


def test_writes_need_csrf_with_cookie_sessions(admin_client, db):
    db.add(author())
    assert admin_client.post("/admin/api/post", json={"title": "x", "author_id": 1}).status_code == HTTPStatus.FORBIDDEN
    assert db.count(Post) == 0


######################################################################
# Permissions and scoping are the admin's own
######################################################################
def test_permissions(client, db):
    db.add(post(status=Status.archived))
    login(client, "editor")
    client.headers["X-CSRF-Token"] = client.app.state.admin.signer.csrf(client.cookies.get("ff_session"))
    assert client.get("/admin/api/secret").status_code == HTTPStatus.FORBIDDEN
    assert client.get("/admin/api/post").status_code == HTTPStatus.OK


def read_only(user, perm, ma):
    return perm == "view"


@pytest.mark.parametrize("admin_options", [{"api": True, "permission": read_only}])
def test_read_only_api(api, db):
    db.add(post())
    assert api.get("/admin/api/post/1").status_code == HTTPStatus.OK
    assert api.patch("/admin/api/post/1", json={"title": "x"}).status_code == HTTPStatus.FORBIDDEN
    assert api.delete("/admin/api/post/1").status_code == HTTPStatus.FORBIDDEN
    assert api.post("/admin/api/author", json={"name": "x"}).status_code == HTTPStatus.FORBIDDEN


######################################################################
# Your own auth for API clients
######################################################################
async def bearer(authorization: str = Header("")) -> str:
    if authorization != "Bearer s3cret":
        raise HTTPException(401, "Bad token")
    return "robot"


@pytest.mark.parametrize("admin_options", [{"api_auth": bearer}])
def test_api_auth_dependency(app, db):
    with TestClient(app) as c:
        assert c.get("/admin/api/post").status_code == HTTPStatus.UNAUTHORIZED
        c.headers["Authorization"] = "Bearer s3cret"
        db_ = type(db)(c.portal.call, app.state.admin)
        db_.add(author())
        r = c.post("/admin/api/post", json={"title": "By robot", "author_id": 1})
        assert r.status_code == HTTPStatus.CREATED, "no CSRF needed: credentials don't ride on cookies"
        assert db_.log()[0].user == "robot"
        c.portal.call(app.state.admin.engine.dispose)


######################################################################
# FastAPI integration
######################################################################
def test_openapi(app):
    spec = app.openapi()
    assert {"/admin/api/post", "/admin/api/post/{pk}"} <= set(spec["paths"])
    assert not any(p.startswith("/admin/") and "/api/" not in p for p in spec["paths"]), "HTML views stay out of the docs"
    schema = spec["components"]["schemas"]["PostCreate"]
    assert set(schema["required"]) == {"title", "author_id"}
    assert schema["properties"]["status"]["anyOf"][0]["enum"] == ["draft", "review", "published", "archived"]
    assert spec["paths"]["/admin/api/post"]["get"]["tags"] == ["admin · Posts"]


def test_api_is_off_by_default():
    from .fixtures import make_engine
    from .server.app import create_app

    assert not any("/api/" in p for p in create_app(make_engine()).openapi()["paths"])


@pytest.mark.parametrize("admin_options", [{"api": True, "cookie_path": "/"}])
def test_current_user_dependency(app, client):
    from fastapi import Depends

    admin = app.state.admin

    @app.get("/me")
    async def me(user: str = Depends(admin.current_user)):
        return {"user": user}

    assert client.get("/me", follow_redirects=False).status_code == HTTPStatus.SEE_OTHER
    login(client)
    assert client.get("/me").json() == {"user": "admin"}


def test_api_slug_is_reserved(admin):
    from fastyfold import ModelAdmin

    class Api(ModelAdmin):
        model = Author
        slug = "api"

    with pytest.raises(ValueError, match="reserved"):
        admin.register(Api)
