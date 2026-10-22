from http import HTTPStatus

import pytest

from .factories import post, secret
from .server.models import Post, Secret, Status


def search_labels(client, q=""):
    return [it["label"] for g in client.get("/admin/_search", params={"q": q}).json()["groups"] for it in g["items"]]


######################################################################
# Per-model has_permission
######################################################################
@pytest.mark.parametrize("path", ["/admin/secret", "/admin/secret/new", "/admin/secret/1", "/admin/secret/1/preview"])
def test_hidden_model_pages_are_forbidden(editor_client, db, path):
    db.add(secret())
    assert editor_client.get(path).status_code == HTTPStatus.FORBIDDEN


@pytest.mark.parametrize("url, data", [
    ("/admin/secret/new", {"value": "x"}),
    ("/admin/secret/1", {"value": "x"}),
    ("/admin/secret/1/delete", {}),
    ("/admin/secret/action", {"action": "delete", "ids": "1"}),
])
def test_hidden_model_writes_are_forbidden(editor_client, submit, db, url, data):
    db.add(secret(value="keep"))
    assert submit(url, data).status_code == HTTPStatus.FORBIDDEN
    assert db.get(Secret, 1).value == "keep"


def test_hidden_model_is_left_out_of_navigation(editor_client):
    page = editor_client.get("/admin/").text
    assert "Secrets" not in page and "/admin/secret" not in page
    assert not any("ecret" in label for label in search_labels(editor_client))


def test_hidden_model_records_are_left_out_of_search(editor_client, db):
    db.add(secret(value="needle"))
    assert "needle" not in search_labels(editor_client, "needle")


def test_owner_sees_hidden_model(admin_client, db):
    db.add(secret(value="needle"))
    assert admin_client.get("/admin/secret").status_code == HTTPStatus.OK
    assert "Secrets" in admin_client.get("/admin/").text
    assert "needle" in search_labels(admin_client, "needle")


######################################################################
# Per-action permission
######################################################################
def test_action_permission(editor_client, submit, db):
    p = db.add(post())
    page = editor_client.get("/admin/post?view=table").text
    assert 'value="publish"' in page and 'value="archive"' not in page
    assert submit("/admin/post/action", {"action": "archive", "ids": str(p.id)}).status_code == HTTPStatus.FORBIDDEN
    assert db.get(Post, p.id).status == Status.draft


def test_action_permission_in_preview(editor_client, db):
    p = db.add(post())
    page = editor_client.get(f"/admin/post/{p.id}/preview").text
    assert 'value="publish"' in page and 'value="archive"' not in page


######################################################################
# Global Admin(permission=...)
######################################################################
def read_only(user, perm, ma):
    return perm == "view"


@pytest.mark.parametrize("admin_options", [{"permission": read_only}])
def test_read_only_pages_hide_writes(admin_client, db):
    p = db.add(post())
    listing = admin_client.get("/admin/post?view=table").text
    assert "data-new" not in listing and 'value="delete"' not in listing and 'value="publish"' not in listing
    form = admin_client.get(f"/admin/post/{p.id}").text
    assert "data-save" not in form and "delete-form" not in form
    assert 'name="title" value="' in form and "disabled" in form
    assert admin_client.get("/admin/post/new").status_code == HTTPStatus.FORBIDDEN
    assert not any(label.startswith("New ") for label in search_labels(admin_client))


@pytest.mark.parametrize("admin_options", [{"permission": read_only}])
def test_read_only_board_field_is_disabled(admin_client, db):
    p = db.add(post())
    form = admin_client.get(f"/admin/post/{p.id}").text
    radios = [line for line in form.splitlines() if 'type="radio" name="status"' in line]
    assert radios and all("disabled" in line for line in radios)


@pytest.mark.parametrize("admin_options", [{"permission": read_only}])
@pytest.mark.parametrize("url, data", [
    ("/admin/post/new", {"title": "x", "author_id": "1", "views": "0", "status": "draft"}),
    ("/admin/post/{pk}", {"title": "x", "author_id": "1", "views": "0", "status": "draft"}),
    ("/admin/post/{pk}/delete", {}),
    ("/admin/post/{pk}/move", {"value": "published"}),
    ("/admin/post/action", {"action": "publish", "ids": "{pk}"}),
    ("/admin/post/action", {"action": "delete", "ids": "{pk}"}),
])
def test_read_only_writes_are_forbidden(admin_client, submit, db, url, data):
    p = db.add(post(title="Original"))
    r = submit(url.format(pk=p.id), {k: v.format(pk=p.id) for k, v in data.items()})
    assert r.status_code == HTTPStatus.FORBIDDEN
    saved = db.get(Post, p.id)
    assert saved.title == "Original" and saved.status == Status.draft and db.count(Post) == 1


def deny_secret_to_all(user, perm, ma):
    return ma.slug != "secret"


@pytest.mark.parametrize("admin_options", [{"permission": deny_secret_to_all}])
def test_global_permission_overrides_model(admin_client):
    assert admin_client.get("/admin/secret").status_code == HTTPStatus.FORBIDDEN
    assert admin_client.get("/admin/post").status_code == HTTPStatus.OK


@pytest.mark.parametrize("admin_options", [{"permission": lambda user, perm, ma: True}])
def test_global_allow_does_not_override_model(editor_client):
    assert editor_client.get("/admin/secret").status_code == HTTPStatus.FORBIDDEN
