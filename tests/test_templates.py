"""Overriding and adding templates with Admin(templates_dir=...)."""

from http import HTTPStatus

import pytest

from .server.app import MODEL_ADMINS


@pytest.fixture
def admin_options(tmp_path):
    (tmp_path / "fastyfold").mkdir()
    (tmp_path / "fastyfold" / "dashboard.html").write_text(
        '{% extends "original/fastyfold/dashboard.html" %}'
        '{% block content %}<p id="banner">Hello from my template, {{ user }}</p>{{ super() }}{% endblock %}')
    (tmp_path / "reports.html").write_text(
        '{% extends "fastyfold/layout.html" %}{% block content %}<h1 id="own">{{ heading }}</h1>{% endblock %}')
    return {"templates_dir": tmp_path}


@pytest.fixture
def models(admin_options):
    return MODEL_ADMINS


def test_override_extends_the_original(admin_client):
    page = admin_client.get("/admin/").text
    assert '<p id="banner">Hello from my template, admin</p>' in page
    assert "Team activity" in page, "the original dashboard still renders inside"


def test_other_templates_are_untouched(admin_client):
    assert admin_client.get("/admin/post").status_code == HTTPStatus.OK


def test_pages_can_render_own_templates(admin, admin_client):
    @admin.page("Own", slug="own")
    async def own(request, user):
        return admin.render(request, "reports.html", user=user, heading="Quarterly")

    page = admin_client.get("/admin/own").text
    assert '<h1 id="own">Quarterly</h1>' in page and 'data-jumps=' in page


def test_several_dirs(tmp_path, admin):
    from fastyfold import Admin

    from .server.app import authenticate

    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    (first / "x.html").write_text("first")
    (second / "x.html").write_text("second")
    (second / "y.html").write_text("only second")
    a = Admin(admin.engine, secret_key="k", authenticate=authenticate, templates_dir=[first, second])
    assert a.templates.env.get_template("x.html").render() == "first"
    assert a.templates.env.get_template("y.html").render() == "only second"
