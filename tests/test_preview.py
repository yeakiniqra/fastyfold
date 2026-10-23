"""The split view's preview panel and board cards: what they show, and what they offer."""

import importlib.metadata

import pytest

import fastyfold

from .factories import post
from .server.app import MODEL_ADMINS, PostAdmin
from .server.models import Status


class DetailedPosts(PostAdmin):
    preview_fields = ["views", "summary", "meta", "rating"]

    def has_object_permission(self, user, perm, obj):
        return perm == "view" or obj.status != Status.archived


@pytest.fixture
def models():
    return (DetailedPosts, *MODEL_ADMINS[1:])


def test_long_text_is_shown_in_full(admin_client, db):
    long = "First line of a long answer.\n" + "word " * 80
    db.add(post(summary=long, meta={"lang": "bn"}, views=7))
    page = admin_client.get("/admin/post/1/preview").text
    assert 'data-preview-text="summary"' in page and 'data-preview-text="meta"' in page
    assert "word word word" in page and "…" not in page.split('data-preview-text="summary"')[1][:2000]
    assert 'title="7"' in page, "short values stay as tiles"


def test_edit_and_delete_only_when_allowed(admin_client, db):
    db.add(post(title="Open"), post(title="Locked", status=Status.archived))
    editable = admin_client.get("/admin/post/1/preview").text
    assert "Edit post" in editable and 'value="delete"' in editable and 'value="publish"' in editable
    locked = admin_client.get("/admin/post/2/preview").text
    assert "Edit post" not in locked and 'value="delete"' not in locked and 'value="publish"' not in locked
    assert "Quick actions" not in locked, "nothing to offer, so no empty section"


@pytest.mark.parametrize("admin_options", [{"permission": lambda user, perm, ma: perm == "view"}])
def test_read_only_preview(admin_client, db):
    db.add(post())
    page = admin_client.get("/admin/post/1/preview").text
    assert "Edit post" not in page and "Quick actions" not in page and "History" in page


def test_board_cards_label_their_numbers(admin_client, db):
    db.add(post(views=42))
    page = admin_client.get("/admin/post?view=board").text
    card = page.split("data-card-fields", 1)[1][:600]
    assert "Views" in card and "42" in card


def test_version_comes_from_the_package():
    assert fastyfold.__version__ == importlib.metadata.version("fastyfold")
