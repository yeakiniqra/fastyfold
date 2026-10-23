"""The ready-made roles editor: Admin(owners=[...]) turns it on."""

from http import HTTPStatus

import pytest

from fastyfold.roles import parse_members

from .factories import author, post, secret
from .fixtures import login
from .server.models import Post, Status


@pytest.fixture
def admin_options():
    return {"owners": ["admin"]}


def make_role(client, csrf, name="Editors", members="editor", grants=(), **extra):
    login(client, "admin")
    return client.post("/admin/_roles/new", data={"_csrf": csrf(), "name": name, "members": members, "grant": list(grants), **extra})


def as_editor(client):
    login(client, "editor")
    return client


def labels(client, q=""):
    return [it["label"] for g in client.get("/admin/_search", params={"q": q}).json()["groups"] for it in g["items"]]


######################################################################
# Without a role you see nothing
######################################################################
def test_no_role_no_access(client, db):
    db.add(post())
    as_editor(client)
    assert client.get("/admin/post").status_code == HTTPStatus.FORBIDDEN
    page = client.get("/admin/").text
    assert 'href="/admin/post"' not in page and 'href="/admin/reports"' not in page
    assert not any(label.startswith("New ") for label in labels(client))


def test_owners_can_do_everything(admin_client, db):
    db.add(secret())
    assert admin_client.get("/admin/secret").status_code == HTTPStatus.OK
    assert admin_client.get("/admin/reports").status_code == HTTPStatus.OK


######################################################################
# Granting
######################################################################
def test_role_grants_exactly_what_is_ticked(client, csrf, db):
    db.add(post(title="Draft one"))
    r = make_role(client, csrf, grants=["post:view", "post:change", "post:publish", "reports:view"])
    assert r.status_code == HTTPStatus.SEE_OTHER and "Saved+role+Editors" in r.headers["location"]
    as_editor(client)
    token = csrf()
    assert client.get("/admin/post").status_code == HTTPStatus.OK
    assert client.get("/admin/reports").status_code == HTTPStatus.OK
    assert client.get("/admin/author").status_code == HTTPStatus.FORBIDDEN
    assert client.get("/admin/post/new").status_code == HTTPStatus.FORBIDDEN, "no add"
    assert client.get("/admin/post/export.csv").status_code == HTTPStatus.FORBIDDEN, "no export"
    assert client.post("/admin/post/1/delete", data={"_csrf": token}).status_code == HTTPStatus.FORBIDDEN
    r = client.post("/admin/post/action", data={"_csrf": token, "action": "publish", "ids": "1"})
    assert r.status_code == HTTPStatus.SEE_OTHER and db.get(Post, 1).status == Status.published
    assert client.post("/admin/post/action", data={"_csrf": token, "action": "archive", "ids": "1"}).status_code == HTTPStatus.FORBIDDEN


def test_navigation_follows_grants(client, csrf, db):
    make_role(client, csrf, grants=["post:view"])
    as_editor(client)
    page = client.get("/admin/").text
    assert 'href="/admin/post"' in page and 'href="/admin/author"' not in page
    assert "Posts" in labels(client) and "Authors" not in labels(client)
    assert 'value="publish"' not in client.get("/admin/post?view=table").text


def test_roles_add_up(client, csrf, db):
    make_role(client, csrf, name="Readers", grants=["post:view"])
    make_role(client, csrf, name="Writers", grants=["author:view"])
    as_editor(client)
    assert client.get("/admin/post").status_code == HTTPStatus.OK and client.get("/admin/author").status_code == HTTPStatus.OK


def test_roles_and_model_rules_must_both_allow(client, csrf, db):
    make_role(client, csrf, grants=["secret:view"])
    as_editor(client)
    assert client.get("/admin/secret").status_code == HTTPStatus.FORBIDDEN, "SecretAdmin.has_permission still says no"


def test_unknown_grants_are_ignored(client, csrf, db):
    make_role(client, csrf, grants=["post:view", "post:explode", "nope:view", "_roles:view"])
    login(client, "admin")
    page = client.get("/admin/_roles/1").text
    assert 'value="post:view" x-model="grants"' in page
    assert "explode" not in page and "nope:view" not in page


######################################################################
# Editing roles
######################################################################
def test_edit_membership_and_grants(client, csrf, db):
    make_role(client, csrf, grants=["post:view"])
    login(client, "admin")
    page = client.get("/admin/_roles/1").text
    assert 'name="members"' in page and ">editor</textarea>" in page
    client.post("/admin/_roles/1", data={"_csrf": csrf(), "name": "Editors", "members": "someone", "grant": ["author:view"]})
    as_editor(client)
    assert client.get("/admin/post").status_code == HTTPStatus.FORBIDDEN, "editor was removed"


def test_delete_role(client, csrf, db):
    make_role(client, csrf, grants=["post:view"])
    login(client, "admin")
    r = client.post("/admin/_roles/1/delete", data={"_csrf": csrf()})
    assert r.status_code == HTTPStatus.SEE_OTHER and "Deleted+role+Editors" in r.headers["location"]
    assert client.get("/admin/_roles/1").status_code == HTTPStatus.NOT_FOUND
    as_editor(client)
    assert client.get("/admin/post").status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("name, error", [("", "This field is required."), ("x" * 81, "under 80 characters")])
def test_role_validation(client, csrf, name, error):
    r = make_role(client, csrf, name=name, grants=["post:view"])
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and error in r.text
    assert """grants: ["post:view"]""" in r.text, "ticks are kept"


def test_duplicate_role_name(client, csrf):
    make_role(client, csrf)
    r = make_role(client, csrf)
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "Another role already has this name." in r.text


def test_roles_list(client, csrf):
    make_role(client, csrf, name="Editors", members="editor\nnusrat", grants=["post:view", "post:change"])
    make_role(client, csrf, name="Readers", members="nusrat", grants=["post:view"])
    page = client.get("/admin/_roles").text
    assert "Editors" in page and "Readers" in page and "editor, nusrat" in page
    assert '<span class="text-right text-xs text-muted">Editors, Readers</span>' in page


def test_matrix_lists_models_pages_and_actions(admin_client):
    page = admin_client.get("/admin/_roles/new").text
    for key in ("post:view", "post:export", "post:publish", "post:archive", "reports:view", "ticket:delete"):
        assert f'value="{key}"' in page, key
    assert 'value="reports:add"' not in page, "pages only have view"


def test_known_users_are_suggested(client, csrf, db):
    login(client, "editor")
    login(client, "admin")
    assert "Known users: editor" in client.get("/admin/_roles/new").text


def test_parse_members():
    assert parse_members(" a, b\n\nb\n c ") == ["a", "b", "c"]


######################################################################
# Who may manage roles
######################################################################
@pytest.mark.parametrize("method, url", [("get", "/admin/_roles"), ("get", "/admin/_roles/new"), ("post", "/admin/_roles/new"),
                                         ("get", "/admin/_roles/1"), ("post", "/admin/_roles/1"), ("post", "/admin/_roles/1/delete")])
def test_only_owners_manage_roles(client, csrf, method, url):
    make_role(client, csrf, grants=["post:view"])
    as_editor(client)
    r = client.get(url) if method == "get" else client.post(url, data={"_csrf": csrf(), "name": "Hijack", "grant": ["secret:view"]})
    assert r.status_code == HTTPStatus.FORBIDDEN
    assert 'href="/admin/_roles"' not in client.get("/admin/").text


def test_role_writes_need_csrf(admin_client):
    assert admin_client.post("/admin/_roles/new", data={"name": "x"}).status_code == HTTPStatus.FORBIDDEN


def test_owner_sees_roles_link_and_palette(admin_client):
    assert 'href="/admin/_roles"' in admin_client.get("/admin/").text
    assert "Roles and permissions" in labels(admin_client, "roles")


def test_role_changes_are_audited_for_owners_only(client, csrf, db):
    make_role(client, csrf, grants=["post:view"])
    login(client, "admin")
    log = client.get("/admin/_log").text
    assert "Role Editors" in log and "added" in log
    as_editor(client)
    assert "Role Editors" not in client.get("/admin/_log").text


def test_roles_off_without_owners():
    from fastapi.testclient import TestClient

    from .fixtures import make_engine
    from .server.app import create_app

    engine = make_engine()
    with TestClient(create_app(engine), follow_redirects=False) as c:
        login(c)
        assert c.get("/admin/_roles").status_code == HTTPStatus.NOT_FOUND
        c.portal.call(engine.dispose)


@pytest.mark.parametrize("admin_options", [{"owners": ["admin"], "api": True}])
def test_api_respects_roles(client, csrf, db):
    db.add(post(), author())
    make_role(client, csrf, grants=["post:view"])
    as_editor(client)
    client.headers["X-CSRF-Token"] = csrf()
    assert client.get("/admin/api/post").status_code == HTTPStatus.OK
    assert client.get("/admin/api/author").status_code == HTTPStatus.FORBIDDEN
    assert client.patch("/admin/api/post/1", json={"title": "x"}).status_code == HTTPStatus.FORBIDDEN
