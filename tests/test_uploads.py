from http import HTTPStatus
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from fastyfold import Admin

from .factories import PNG, author
from .server.app import AuthorAdmin, authenticate
from .server.models import Author


@pytest.fixture
def media(admin):
    return admin.storage.root


def upload(submit, url="/admin/author/new", files=None, **data):
    return submit(url, {"name": "Nusrat", **data}, files=files or {})


def stored(media) -> list[str]:
    return sorted(str(p.relative_to(media)) for p in Path(media).rglob("*") if p.is_file())


def test_upload_image_and_file(admin_client, submit, db, media):
    r = upload(submit, files={"photo": ("me.PNG", PNG, "image/png"), "cv": ("cv.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == HTTPStatus.SEE_OTHER, r.text
    a = db.get(Author, 1)
    assert a.photo.startswith("author/") and a.photo.endswith(".png")
    assert a.cv.startswith("author/") and a.cv.endswith(".pdf")
    assert stored(media) == sorted([a.photo, a.cv])
    assert (media / a.photo).read_bytes() == PNG


def test_files_show_in_list_and_form(admin_client, submit, db):
    upload(submit, files={"photo": ("me.png", PNG, "image/png"), "cv": ("cv.pdf", b"%PDF", "application/pdf")})
    a = db.get(Author, 1)
    form = admin_client.get("/admin/author/1").text
    assert 'enctype="multipart/form-data"' in form
    assert f'<img src="/admin/_media/{a.photo}" alt="Current photo"' in form
    assert f'href="/admin/_media/{a.cv}"' in form and 'name="cv__clear"' in form


def test_image_is_served_inline(admin_client, submit, db):
    upload(submit, files={"photo": ("me.png", PNG, "image/png")})
    r = admin_client.get(f"/admin/_media/{db.get(Author, 1).photo}")
    assert r.status_code == HTTPStatus.OK and r.content == PNG
    assert r.headers["content-type"] == "image/png" and r.headers["content-disposition"].startswith("inline")
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]


def test_other_files_download(admin_client, submit, db):
    upload(submit, files={"cv": ("page.html", b"<script>alert(1)</script>", "text/html")})
    r = admin_client.get(f"/admin/_media/{db.get(Author, 1).cv}")
    assert r.headers["content-type"] == "application/octet-stream"
    assert r.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize("name, data", [
    ("fake.png", b"not really a png"),
    ("pic.svg", b"<svg onload='alert(1)'/>"),
    ("pic.jpg", PNG),
    ("noext", PNG),
])
def test_images_are_checked(admin_client, submit, db, media, name, data):
    r = upload(submit, files={"photo": (name, data, "image/png")})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "Upload a PNG, JPEG, GIF or WebP image." in r.text
    assert db.count(Author) == 0 and stored(media) == []


def test_empty_file(admin_client, submit, db):
    r = upload(submit, files={"cv": ("empty.txt", b"", "text/plain")})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "The file is empty." in r.text


@pytest.mark.parametrize("admin_options", [{"max_upload": 16}])
def test_size_limit(admin_client, submit, db):
    r = upload(submit, files={"cv": ("big.txt", b"x" * 17, "text/plain")})
    assert r.status_code == HTTPStatus.UNPROCESSABLE_ENTITY and "Files can be up to 1 KB." in r.text


def test_keep_replace_and_remove(admin_client, submit, db, media):
    upload(submit, files={"photo": ("a.png", PNG, "image/png")})
    first = db.get(Author, 1).photo
    upload(submit, "/admin/author/1", name="Renamed")
    assert db.get(Author, 1).photo == first, "no new upload keeps the file"
    upload(submit, "/admin/author/1", files={"photo": ("b.png", PNG + b"2", "image/png")})
    second = db.get(Author, 1).photo
    assert second != first and stored(media) == [second], "the replaced file is deleted"
    upload(submit, "/admin/author/1", photo__clear="1")
    assert db.get(Author, 1).photo is None and stored(media) == []
    changes = [e.changed.get("photo") for e in db.log()]
    assert changes[0] == [second.rsplit("/", 1)[1], None]


def test_deleting_record_deletes_files(admin_client, submit, db, media):
    upload(submit, files={"photo": ("a.png", PNG, "image/png")})
    upload(submit, files={"photo": ("b.png", PNG, "image/png")}, name="Second")
    submit("/admin/author/1/delete")
    assert len(stored(media)) == 1
    submit("/admin/author/action", {"action": "delete", "ids": "2"})
    assert stored(media) == []


def test_media_needs_sign_in(client):
    assert client.get("/admin/_media/author/x.png").status_code == HTTPStatus.SEE_OTHER


@pytest.mark.parametrize("key", ["missing.png", "..%2F..%2Fetc%2Fpasswd", "%2Fetc%2Fpasswd", "author/..%2F..%2Fsecret"])
def test_media_stays_inside_its_folder(admin_client, media, key):
    (media.parent / "secret").write_text("nope")
    assert admin_client.get(f"/admin/_media/{key}").status_code == HTTPStatus.NOT_FOUND


def test_file_fields_need_storage():
    admin = Admin(create_async_engine("sqlite+aiosqlite://"), secret_key="k", authenticate=authenticate)
    with pytest.raises(ValueError, match="media_dir"):
        admin.register(AuthorAdmin)


class MemoryStorage:
    def __init__(self):
        self.files = {}

    async def save(self, name, data, content_type):
        self.files[name] = data
        return name

    async def delete(self, key):
        self.files.pop(key, None)

    def url(self, key):
        return f"https://cdn.example/{key}"


@pytest.mark.parametrize("admin_options", [{"storage": MemoryStorage()}])
def test_custom_storage(admin_client, submit, admin, db):
    upload(submit, files={"photo": ("a.png", PNG, "image/png")})
    key = db.get(Author, 1).photo
    assert admin.storage.files[key] == PNG
    assert f'src="https://cdn.example/{key}"' in admin_client.get("/admin/author/1").text
    assert admin_client.get(f"/admin/_media/{key}").status_code == HTTPStatus.NOT_FOUND


def test_display_shows_file_name(admin):
    assert admin.models["author"].display(author(cv="author/abc123.pdf"), "cv") == "abc123.pdf"
