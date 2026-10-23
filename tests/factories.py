"""Unsaved rows with sensible defaults. Save them with `db.add(...)`."""

from itertools import count

from .server.models import Author, Comment, Post, Secret, Tag

_n = count(1)


def author(**kw) -> Author:
    n = next(_n)
    return Author(**{"name": f"Author {n}", "email": f"author{n}@example.com", **kw})


def post(**kw) -> Post:
    if "author" not in kw and "author_id" not in kw:
        kw["author"] = author()
    return Post(**{"title": f"Post {next(_n)}", **kw})


def tag(**kw) -> Tag:
    return Tag(**{"code": f"T{next(_n)}", **kw})


def secret(**kw) -> Secret:
    return Secret(**{"value": f"secret {next(_n)}", **kw})


def comment(**kw) -> Comment:
    return Comment(**{"body": f"Comment {next(_n)}", **kw})


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
