"""Column types beyond the basics: UUID keys, Time, and types fastyfold only displays."""

import uuid
from datetime import time, timedelta
from http import HTTPStatus

import pytest
from sqlalchemy import inspect

from fastyfold.fields import FieldError, fields_for

from .server.models import Seat, Ticket


def test_kinds():
    f = fields_for(inspect(Ticket))
    assert {n: x.kind for n, x in f.items()} == {"id": "uuid", "code": "str", "opens": "time", "window": "other", "blob": "other"}
    assert f["window"].readonly and f["blob"].readonly and not f["window"].required
    seat = fields_for(inspect(Seat))["ticket_id"]
    assert seat.kind == "fk" and seat.key_kind == "uuid"


def test_parse_and_show():
    f = fields_for(inspect(Ticket))
    u = uuid.uuid4()
    assert f["id"].parse(str(u)) == u
    assert f["opens"].parse("09:30") == time(9, 30) and f["opens"].form_value(time(9, 30, 15)) == "09:30"
    assert f["opens"].display(time(18, 5)) == "18:05"
    assert fields_for(inspect(Seat))["ticket_id"].parse(str(u)) == u
    for name, raw in [("id", "nope"), ("opens", "25:00")]:
        with pytest.raises(FieldError):
            f[name].parse(raw)


def test_typedecorator_uses_its_impl():
    from sqlalchemy import Integer, String
    from sqlalchemy.types import TypeDecorator

    from fastyfold.fields import _type_kind

    class Cents(TypeDecorator):
        impl = Integer

    class Slug(TypeDecorator):
        impl = String(40)

    assert _type_kind(Cents()) == "int" and _type_kind(Slug()) == "str"


@pytest.fixture
def ticket(admin_client, db):
    return db.add(Ticket(code="VIP", opens=time(19, 0), window=timedelta(hours=2), blob=b"\x00"))


def test_uuid_record_pages(admin_client, ticket):
    for url in ("/admin/ticket", f"/admin/ticket/{ticket.id}", f"/admin/ticket/{ticket.id}/preview", "/admin/ticket?view=split"):
        assert admin_client.get(url).status_code == HTTPStatus.OK, url
    form = admin_client.get(f"/admin/ticket/{ticket.id}").text
    assert 'name="opens" value="19:00"' in form and 'type="time"' in form
    assert 'name="window"' not in form and 'name="blob"' not in form, "display-only types stay off the form"
    assert admin_client.get(f"/admin/ticket/{uuid.uuid4()}").status_code == HTTPStatus.NOT_FOUND
    assert admin_client.get("/admin/ticket/not-a-uuid").status_code == HTTPStatus.NOT_FOUND


def test_edit_keeps_display_only_columns(submit, db, ticket):
    r = submit(f"/admin/ticket/{ticket.id}", {"code": "VIP2", "opens": "20:15"})
    assert r.status_code == HTTPStatus.SEE_OTHER
    saved = db.get(Ticket, ticket.id)
    assert (saved.code, saved.opens, saved.window, saved.blob) == ("VIP2", time(20, 15), timedelta(hours=2), b"\x00")


def test_create_with_uuid_key(submit, db, admin_client):
    r = submit("/admin/ticket/new", {"code": "GA", "_continue": "1"})
    assert r.status_code == HTTPStatus.SEE_OTHER
    pk = r.headers["location"].split("/")[3].split("?")[0]
    assert db.get(Ticket, uuid.UUID(pk)).code == "GA"


def test_fk_to_uuid_key(submit, db, ticket):
    r = submit("/admin/seat/new", {"label": "A1", "ticket_id": str(ticket.id)})
    assert r.status_code == HTTPStatus.SEE_OTHER
    assert db.get(Seat, 1).ticket_id == ticket.id
    assert submit("/admin/seat/new", {"label": "A2", "ticket_id": "nope"}).status_code == HTTPStatus.UNPROCESSABLE_ENTITY


def test_uuid_lookup_and_filter(admin_client, db, ticket):
    db.add(Seat(label="A1", ticket_id=ticket.id), Seat(label="B2", ticket_id=db.add(Ticket(code="Other")).id))
    assert admin_client.get("/admin/seat/_lookup/ticket_id", params={"q": "vip"}).json()["results"] == [{"value": str(ticket.id), "label": "VIP"}]
    page = admin_client.get(f"/admin/seat?f_ticket_id={ticket.id}").text
    assert "A1" in page and "B2" not in page and "Ticket: VIP" in page


def test_bulk_action_with_uuid_keys(submit, db, ticket):
    r = submit("/admin/ticket/action", {"action": "delete", "ids": [str(ticket.id)]})
    assert r.status_code == HTTPStatus.SEE_OTHER and db.get(Ticket, ticket.id) is None
    assert submit("/admin/ticket/action", {"action": "delete", "ids": ["zzz"]}).status_code == HTTPStatus.BAD_REQUEST
