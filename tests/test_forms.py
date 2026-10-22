from datetime import datetime
from decimal import Decimal
from http import HTTPStatus

import pytest

from .factories import author, post, tag
from .server.models import Author, Post, Status, Tag


@pytest.fixture
def writer(admin_client, db):
    return db.add(author(name="Writer"))


def post_data(writer, **kw):
    return {"title": "Fresh", "author_id": str(writer.id), "views": "3", "status": "review", **kw}


######################################################################
# New form
######################################################################
def test_new_form_prefills_defaults(admin_client, writer):
    page = admin_client.get("/admin/post/new").text
    assert 'name="views" value="0"' in page
    assert 'name="status" value="draft" class="peer sr-only" checked' in page
    assert f'<option value="{writer.id}" >Writer</option>' in page
    assert "New post" in page and "data-save" in page


def test_new_form_bool_default(admin_client):
    assert "x-data=\"{ on: true }\"" in admin_client.get("/admin/author/new").text


def test_fieldsets(admin_client):
    page = admin_client.get("/admin/author/new").text
    assert "Profile" in page and "About" in page and 'href="#section-2"' in page


def test_auto_fields_are_left_off_the_form(admin_client):
    assert 'name="joined"' not in admin_client.get("/admin/author/new").text


def test_choice_widgets(admin_client):
    tag_form = admin_client.get("/admin/tag/new").text
    assert 'name="kind" value="series"' in tag_form and 'name="code"' not in tag_form


######################################################################
# Create
######################################################################
def test_create(admin_client, submit, db, writer, admin):
    r = submit("/admin/post/new", post_data(writer, rating="4.25", meta='{"lang": "bn"}', published_at="2026-10-22T09:30"))
    assert r.status_code == HTTPStatus.SEE_OTHER
    assert r.headers["location"] == "/admin/post?msg=Saved+Fresh"
    saved = db.get(Post, 1)
    assert saved.title == "Fresh" and saved.views == 3 and saved.status == Status.review
    assert saved.rating == Decimal("4.25") and saved.meta == {"lang": "bn"} and saved.published_at == datetime(2026, 10, 22, 9, 30)
    assert admin.models["post"].saved == [("Fresh", True)], "save hook runs"


def test_create_and_keep_editing(submit, writer):
    r = submit("/admin/post/new", post_data(writer, _continue="1"))
    assert r.headers["location"].startswith("/admin/post/1?msg=")


def test_create_and_add_another(submit, writer):
    r = submit("/admin/post/new", post_data(writer, _another="1"))
    assert r.headers["location"].startswith("/admin/post/new?msg=")


def test_success_message_is_shown(admin_client):
    assert "Saved Fresh" in admin_client.get("/admin/post?msg=Saved+Fresh").text


def test_unchecked_bool_saves_false(admin_client, submit, db):
    submit("/admin/author/new", {"name": "Quiet"})
    assert db.get(Author, 1).active is False


def test_readonly_field_is_not_saved(submit, db, admin_client):
    t = db.add(tag(code="KEEP"))
    submit(f"/admin/tag/{t.id}", {"code": "HACKED", "kind": "series"})
    saved = db.get(Tag, t.id)
    assert saved.code == "KEEP" and saved.kind == "series"


######################################################################
# Validation
######################################################################
def test_required_fields(submit, db, writer):
    r = submit("/admin/post/new", {"author_id": str(writer.id), "views": "1", "status": "draft"})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert "This field is required." in r.text and "1 field needs attention" in r.text
    assert db.count(Post) == 0


@pytest.mark.parametrize("field, raw, message", [
    ("views", "abc", "Enter a valid whole number."),
    ("rating", "x", "Enter a valid number."),
    ("meta", "{bad", "Enter a valid JSON value."),
    ("published_at", "soon", "Enter a valid date and time."),
    ("status", "deleted", "Select a valid option."),
])
def test_parse_errors(submit, db, writer, field, raw, message):
    r = submit("/admin/post/new", post_data(writer, **{field: raw}))
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and message in r.text
    assert db.count(Post) == 0


def test_errors_keep_what_the_user_typed(submit, writer):
    r = submit("/admin/post/new", post_data(writer, title="Typed title", views="abc"))
    assert 'value="Typed title"' in r.text and 'value="abc"' in r.text


def test_several_errors(submit, writer):
    r = submit("/admin/post/new", post_data(writer, title="", views="x"))
    assert "2 fields need attention" in r.text


def test_schema_validation(submit, db, writer):
    r = submit("/admin/post/new", post_data(writer, title="Buy spam now"))
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "No spam, please" in r.text
    assert db.count(Post) == 0


def test_choices_override_validation(admin_client, submit, db):
    r = submit("/admin/tag/new", {"kind": "other"})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "Select a valid option." in r.text


######################################################################
# Edit
######################################################################
def test_edit_form_shows_values(admin_client, db):
    p = db.add(post(title="Existing", views=7, status=Status.published))
    page = admin_client.get(f"/admin/post/{p.id}").text
    assert 'value="Existing"' in page and 'name="views" value="7"' in page
    assert 'name="status" value="published" class="peer sr-only" checked' in page
    assert f'action="/admin/post/{p.id}/delete"' in page


def test_edit_shows_auto_fields_in_record_card(admin_client, db):
    a = db.add(author(name="Has joined"))
    page = admin_client.get(f"/admin/author/{a.id}").text
    assert "Joined" in page and 'name="joined"' not in page


def test_edit(submit, db, admin, writer):
    p = db.add(post(title="Before", author=writer, views=1))
    r = submit(f"/admin/post/{p.id}", post_data(writer, title="After", views="1", status="draft"))
    assert r.status_code == HTTPStatus.SEE_OTHER and r.headers["location"] == "/admin/post?msg=Saved+After"
    assert db.get(Post, p.id).title == "After"
    assert admin.models["post"].saved == [("After", False)]


def test_edit_and_keep_editing(submit, db, writer):
    p = db.add(post(author=writer))
    r = submit(f"/admin/post/{p.id}", post_data(writer, _continue="1"))
    assert r.headers["location"].startswith(f"/admin/post/{p.id}?msg=")


def test_edit_validation_rerenders_form(submit, db, writer):
    p = db.add(post(title="Before", author=writer))
    r = submit(f"/admin/post/{p.id}", post_data(writer, views="many"))
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "Enter a valid whole number." in r.text
    assert db.get(Post, p.id).title == "Before"


@pytest.mark.parametrize("pk", ["99", "abc"])
def test_missing_record(admin_client, submit, pk):
    assert admin_client.get(f"/admin/post/{pk}").status_code == HTTPStatus.NOT_FOUND
    assert submit(f"/admin/post/{pk}", {"title": "x"}).status_code == HTTPStatus.NOT_FOUND


######################################################################
# Delete
######################################################################
def test_delete(admin_client, submit, db):
    p = db.add(post(title="Doomed"))
    r = submit(f"/admin/post/{p.id}/delete")
    assert r.status_code == HTTPStatus.SEE_OTHER and r.headers["location"] == "/admin/post?msg=Deleted+Doomed"
    assert db.get(Post, p.id) is None


def test_delete_confirm_escapes_the_label(admin_client, db):
    p = db.add(post(title="x'); alert(1); ('\"<b>"))
    for url in (f"/admin/post/{p.id}", f"/admin/post/{p.id}/preview"):
        page = admin_client.get(url).text
        assert "x\\u0027); alert(1); (\\u0027\\\"\\u003cb\\u003e?" in page
        assert "confirm('Delete x'" not in page


@pytest.mark.parametrize("pk", ["99", "abc"])
def test_delete_missing_record(submit, admin_client, pk):
    assert submit(f"/admin/post/{pk}/delete").status_code == HTTPStatus.NOT_FOUND
