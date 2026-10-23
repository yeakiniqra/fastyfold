from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import inspect

from fastyfold.fields import Field, FieldError, fields_for, humanize

from .server.models import Author, Post, Status, Tag


@pytest.fixture
def post_fields():
    return fields_for(inspect(Post))


######################################################################
# Column mapping
######################################################################
def test_kind_per_column_type(post_fields):
    assert {n: f.kind for n, f in post_fields.items()} == {
        "id": "int", "title": "str", "author_id": "fk", "status": "choice", "views": "int",
        "rating": "decimal", "meta": "json", "published_at": "datetime", "summary": "text", "tags": "m2m",
    }
    author = fields_for(inspect(Author))
    assert author["bio"].kind == "text" and author["active"].kind == "bool" and author["born"].kind == "date"


def test_flags(post_fields):
    assert post_fields["id"].readonly and not post_fields["id"].required
    assert post_fields["title"].required and post_fields["views"].required
    assert not post_fields["rating"].required and not post_fields["summary"].required
    author = fields_for(inspect(Author))
    assert not author["active"].required, "bool fields are never required"
    assert author["joined"].auto and not author["name"].auto


def test_defaults(post_fields):
    assert post_fields["status"].default is Status.draft
    assert post_fields["views"].default == 0
    assert post_fields["title"].default is None


def test_readonly_option():
    assert fields_for(inspect(Tag), readonly={"code"})["code"].readonly


def test_enum_choices(post_fields):
    f = post_fields["status"]
    assert f.enum_cls is Status
    assert f.choices == [("draft", "Draft"), ("review", "Review"), ("published", "Published"), ("archived", "Archived")]


def test_choices_override_accepts_values_and_pairs():
    f = fields_for(inspect(Tag), choices={"kind": ["topic", ("series", "Series")]})["kind"]
    assert f.kind == "choice" and f.choices == [("topic", "Topic"), ("series", "Series")]
    assert f.parse("series") == "series"


def test_fk_relation(post_fields):
    f = post_fields["author_id"]
    assert f.label == "Author" and f.relation == "author" and f.target is Author


def test_humanize():
    assert humanize("published_at") == "Published at"
    assert humanize("_x_") == "X"


######################################################################
# Parsing form input
######################################################################
@pytest.mark.parametrize("name, raw, value", [
    ("views", "42", 42),
    ("views", " 7 ", 7),
    ("author_id", "3", 3),
    ("rating", "4.5", Decimal("4.5")),
    ("status", "review", Status.review),
    ("published_at", "2026-10-22T09:30", datetime(2026, 10, 22, 9, 30)),
    ("meta", '{"a": [1, 2]}', {"a": [1, 2]}),
    ("title", "  Hello  ", "Hello"),
    ("summary", "  keep spaces\n", "  keep spaces\n"),
    ("rating", "", None),
    ("summary", "", None),
])
def test_parse(post_fields, name, raw, value):
    assert post_fields[name].parse(raw) == value


@pytest.mark.parametrize("raw, value", [("on", True), ("true", True), ("1", True), ("yes", True), ("", False), (None, False), ("off", False)])
def test_parse_bool(raw, value):
    assert fields_for(inspect(Author))["active"].parse(raw) is value


def test_parse_date():
    assert fields_for(inspect(Author))["born"].parse("1990-01-31") == date(1990, 1, 31)


@pytest.mark.parametrize("name, raw, message", [
    ("title", "", "This field is required."),
    ("title", None, "This field is required."),
    ("views", "abc", "Enter a valid whole number."),
    ("views", "1.5", "Enter a valid whole number."),
    ("rating", "x", "Enter a valid number."),
    ("published_at", "yesterday", "Enter a valid date and time."),
    ("meta", "{nope", "Enter a valid JSON value."),
    ("status", "deleted", "Select a valid option."),
    ("author_id", "bob", "Enter a valid option."),
])
def test_parse_errors(post_fields, name, raw, message):
    with pytest.raises(FieldError, match=message.replace(".", r"\.")):
        post_fields[name].parse(raw)


######################################################################
# Values back out: form inputs and display
######################################################################
@pytest.mark.parametrize("name, value, out", [
    ("title", None, ""),
    ("status", Status.published, "published"),
    ("published_at", datetime(2026, 1, 2, 3, 4, 5), "2026-01-02T03:04"),
    ("meta", {"a": 1}, '{\n  "a": 1\n}'),
    ("views", 3, "3"),
])
def test_form_value(post_fields, name, value, out):
    assert post_fields[name].form_value(value) == out


@pytest.mark.parametrize("name, value, out", [
    ("title", None, "—"),
    ("status", Status.review, "Review"),
    ("status", "review", "Review"),
    ("published_at", datetime(2026, 1, 2, 3, 4), "Jan 02, 03:04"),
    ("rating", Decimal("1234.5"), "1,234.50"),
    ("views", 5, "5"),
])
def test_display(post_fields, name, value, out):
    assert post_fields[name].display(value) == out


def test_display_bool_and_date():
    f = fields_for(inspect(Author))
    assert f["active"].display(True) == "Yes" and f["active"].display(False) == "No"
    assert f["born"].display(date(1990, 1, 31)) == "Jan 31, 1990"


def test_unknown_choice_displays_raw_value():
    assert Field("x", "X", "choice", choices=[("a", "A")]).display("zzz") == "zzz"
