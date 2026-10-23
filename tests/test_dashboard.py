from datetime import datetime
from http import HTTPStatus

import pytest
from fastapi.testclient import TestClient

import fastyfold.admin
from fastyfold import Admin

from .factories import author, post
from .fixtures import login, make_engine
from .server.app import AuthorAdmin, create_app
from .server.models import Status


def test_dashboard(admin_client, db):
    db.add(post(title="Latest one", status=Status.published), post(status=Status.draft), author())
    page = admin_client.get("/admin/").text
    assert ", admin</h1>" in page
    assert "You have <b class=\"text-accent\">2</b> posts across 6 models." in page
    assert "Posts by status" in page and "Latest posts" in page and "Latest one" in page
    assert "Press G then P" in page


def test_dashboard_counts(admin_client, db):
    db.add(author(), author(), author())
    page = admin_client.get("/admin/").text
    assert '<div class="mt-4 text-3xl font-extrabold">3</div>' in page


def test_dashboard_activity(admin_client, submit, db):
    p = db.add(post(title="Busy"))
    submit(f"/admin/post/{p.id}/move", {"value": "review"})
    page = admin_client.get("/admin/").text
    assert "Team activity" in page and "changed" in page and "Busy" in page


@pytest.mark.parametrize("hour, greeting", [(8, "Good morning"), (13, "Good afternoon"), (21, "Good evening")])
def test_greeting(monkeypatch, hour, greeting):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, hour)

    monkeypatch.setattr(fastyfold.admin, "datetime", Clock)
    assert Admin._greeting() == greeting


def test_dashboard_without_board_model():
    engine = make_engine()
    with TestClient(create_app(engine, models=[AuthorAdmin]), follow_redirects=False) as c:
        login(c)
        page = c.get("/admin/").text
        c.portal.call(engine.dispose)
    assert "At a glance" in page and "Latest authors" in page


def test_dashboard_without_models():
    engine = make_engine()
    with TestClient(create_app(engine, models=[]), follow_redirects=False) as c:
        login(c)
        r = c.get("/admin/")
        c.portal.call(engine.dispose)
    assert r.status_code == HTTPStatus.OK and "Register a model to get started." in r.text
