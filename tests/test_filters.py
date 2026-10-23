"""Foreign-key and number-range filters. Choice, bool and date filters live in test_changelist.py."""

from http import HTTPStatus

import pytest

from .factories import author, post


@pytest.fixture
def seeded(admin_client, db):
    nusrat, arif = author(name="Nusrat"), author(name="Arif")
    db.add(post(title="Low", views=1, author=nusrat), post(title="Mid", views=10, author=nusrat),
           post(title="High", views=100, author=arif))
    return nusrat, arif


def titles(page):
    """Which posts are listed (in a fixed order, not page order)."""
    return [t for t in ("Low", "Mid", "High") if f'<span class="font-semibold">{t}</span>' in page]


def test_fk_filter(admin_client, seeded):
    nusrat, _ = seeded
    page = admin_client.get(f"/admin/post?view=table&f_author_id={nusrat.id}").text
    assert titles(page) == ["Low", "Mid"]
    assert "Author: Nusrat" in page


@pytest.mark.parametrize("raw", ["abc", "999"])
def test_fk_filter_with_unknown_id(admin_client, seeded, raw):
    r = admin_client.get(f"/admin/post?view=table&f_author_id={raw}")
    assert r.status_code == HTTPStatus.OK


@pytest.mark.parametrize("raw, expected, chip", [
    ("5..50", ["Mid"], "Views: 5 – 50"),
    ("10..", ["Mid", "High"], "Views: ≥ 10"),
    ("..10", ["Low", "Mid"], "Views: ≤ 10"),
    ("100", ["High"], "Views: 100"),
])
def test_range_filter(admin_client, seeded, raw, expected, chip):
    page = admin_client.get("/admin/post", params={"view": "table", "f_views": raw}).text
    assert titles(page) == expected
    assert chip in page


@pytest.mark.parametrize("raw", ["a..b", "..", "x"])
def test_bad_range_is_ignored(admin_client, seeded, raw):
    page = admin_client.get("/admin/post", params={"view": "table", "f_views": raw}).text
    assert titles(page) == ["Low", "Mid", "High"] and "Views:" not in page


def test_filters_combine(admin_client, seeded):
    nusrat, _ = seeded
    page = admin_client.get(f"/admin/post?view=table&f_author_id={nusrat.id}&f_views=5..").text
    assert titles(page) == ["Mid"]


def test_filter_panel_widgets(admin_client, seeded):
    nusrat, _ = seeded
    page = admin_client.get(f"/admin/post?view=table&f_author_id={nusrat.id}&f_views=5..50").text
    assert 'name: "f_author_id", url: "/admin/post/_lookup/author_id"' in page
    assert f'selected: [{{"label": "Nusrat", "value": "{nusrat.id}"}}]' in page
    assert """x-data='{ lo: "5", hi: "50" }'""" in page and 'name="f_views"' in page


def test_chip_removes_its_filter(admin_client, seeded):
    nusrat, _ = seeded
    page = admin_client.get(f"/admin/post?view=table&f_author_id={nusrat.id}&f_views=5..50").text
    assert f'href="/admin/post?view=table&amp;f_author_id={nusrat.id}"' in page, "the Views chip drops only f_views"
