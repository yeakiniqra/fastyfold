from datetime import date, datetime, timedelta
from http import HTTPStatus

import pytest

from .factories import author, post, tag
from .server.models import Status


def order_of(text, *needles):
    """True if the needles appear in this order in the page."""
    positions = [text.index(n) for n in needles]
    return positions == sorted(positions)


######################################################################
# Views
######################################################################
def test_board_is_default_when_model_has_board_field(admin_client, db):
    db.add(post(title="Card"))
    page = admin_client.get("/admin/post").text
    assert 'data-column="draft"' in page and 'data-pk="1"' in page


def test_table_is_default_otherwise(admin_client, db):
    db.add(author(name="Nusrat"))
    page = admin_client.get("/admin/author").text
    assert 'name="ids" value="1"' in page and "data-column" not in page


def test_board_falls_back_to_table_without_board_field(admin_client):
    page = admin_client.get("/admin/author?view=board").text
    assert "data-column" not in page and "<table" in page
    assert 'data-view-key="2"' not in page, "no Board switch for models without board_field"


@pytest.mark.parametrize("view", ["table", "board", "split"])
def test_each_view_renders(admin_client, db, view):
    db.add(post(title="Visible row"))
    r = admin_client.get(f"/admin/post?view={view}")
    assert r.status_code == HTTPStatus.OK and "Visible row" in r.text


def test_table_columns(admin_client, db):
    db.add(post(title="Hello", author=author(name="Lena"), status=Status.review, views=12))
    page = admin_client.get("/admin/post?view=table").text
    for label in ("ID", "Title", "Author", "Status", "Views", "Published at"):
        assert label in page
    assert "Lena" in page and "Review" in page and "#1" in page


def test_split_view_rows_load_preview(admin_client, db):
    p = db.add(post())
    page = admin_client.get("/admin/post?view=split").text
    assert f'hx-get="/admin/post/{p.id}/preview"' in page and 'hx-trigger="click, load"' in page


def test_preview(admin_client, db):
    p = db.add(post(title="Previewed", views=99, author=author(name="Rahim")))
    r = admin_client.get(f"/admin/post/{p.id}/preview")
    assert r.status_code == HTTPStatus.OK
    assert "Previewed" in r.text and "99" in r.text and f'href="/admin/post/{p.id}"' in r.text
    assert 'value="publish"' in r.text and "<html" not in r.text, "preview is a partial"


def test_preview_missing_record(admin_client):
    assert admin_client.get("/admin/post/99/preview").status_code == HTTPStatus.NOT_FOUND


def test_board_puts_cards_in_their_column(admin_client, db):
    db.add(post(title="Drafty", status=Status.draft), post(title="Done", status=Status.published))
    page = admin_client.get("/admin/post?view=board").text
    assert order_of(page, 'data-column="draft"', "Drafty", 'data-column="review"', 'data-column="published"', "Done", 'data-column="archived"')


def test_empty_state(admin_client):
    page = admin_client.get("/admin/post?view=table").text
    assert "No posts found" in page and "Add the first one" in page


def test_unknown_model(admin_client):
    assert admin_client.get("/admin/nope").status_code == HTTPStatus.NOT_FOUND


def test_saved_views_show_counts(admin_client, db):
    db.add(post(status=Status.draft), post(status=Status.draft), post(status=Status.published))
    page = admin_client.get("/admin/post").text
    assert "All posts" in page
    for label, n in (("Draft", 2), ("Published", 1), ("Review", 0)):
        assert f'<span class="flex-1">{label}</span><span class="text-xs text-faint">{n}</span>' in page


######################################################################
# Search
######################################################################
def test_search(admin_client, db):
    db.add(post(title="Jamdani scarf"), post(title="Clay tea set"))
    page = admin_client.get("/admin/post?view=table&q=jamdani").text
    assert "Jamdani scarf" in page and "Clay tea set" not in page
    assert "Search: “jamdani”" in page


def test_search_follows_relation(admin_client, db):
    db.add(post(title="By Nusrat", author=author(name="Nusrat Jahan")), post(title="By Arif", author=author(name="Arif")))
    page = admin_client.get("/admin/post?view=table&q=nusrat").text
    assert "By Nusrat" in page and "By Arif" not in page


@pytest.mark.parametrize("q", ["%", "_"])
def test_search_wildcards_are_literal(admin_client, db, q):
    db.add(post(title=f"100{q} done"), post(title="Unrelated row"))
    page = admin_client.get("/admin/post?view=table", params={"q": q}).text
    assert f"100{q} done" in page and "Unrelated row" not in page


def test_search_without_matches(admin_client, db):
    db.add(post())
    assert "No posts found" in admin_client.get("/admin/post?view=table&q=zzz").text


######################################################################
# Filters
######################################################################
def test_choice_filter(admin_client, db):
    db.add(post(title="Drafty", status=Status.draft), post(title="Done", status=Status.published))
    page = admin_client.get("/admin/post?view=table&f_status=published").text
    assert "Done" in page and "Drafty" not in page
    assert "Status: Published" in page


def test_bool_filter(admin_client, db):
    db.add(author(name="Awake", active=True), author(name="Asleep", active=False))
    on = admin_client.get("/admin/author?f_active=1").text
    off = admin_client.get("/admin/author?f_active=0").text
    assert "Awake" in on and "Asleep" not in on
    assert "Asleep" in off and "Awake" not in off


@pytest.mark.parametrize("value, seen, hidden", [("today", ["Now"], ["Week", "Old"]), ("7d", ["Now", "Week"], ["Old"]),
                                                 ("30d", ["Now", "Week"], ["Old"])])
def test_datetime_filter(admin_client, db, value, seen, hidden):
    now = datetime.now()
    db.add(post(title="Now", published_at=now), post(title="Week", published_at=now - timedelta(days=3)),
           post(title="Old", published_at=now - timedelta(days=90)))
    page = admin_client.get(f"/admin/post?view=table&f_published_at={value}").text
    assert all(t in page for t in seen) and not any(t in page for t in hidden)


def test_date_filter(admin_client, db):
    db.add(author(name="Young", born=date.today()), author(name="Ancient", born=date(1900, 1, 1)))
    page = admin_client.get("/admin/author?f_born=30d").text
    assert "Young" in page and "Ancient" not in page


@pytest.mark.parametrize("query", ["f_status=nope", "f_published_at=yesterday", "f_title=Hidden", "f_views=0"])
def test_invalid_or_unlisted_filters_are_ignored(admin_client, db, query):
    db.add(post(title="Hidden"), post(title="Other"))
    r = admin_client.get(f"/admin/post?view=table&{query}")
    assert r.status_code == HTTPStatus.OK and "Hidden" in r.text and "Other" in r.text


def test_filters_combine_with_search(admin_client, db):
    db.add(post(title="Alpha draft", status=Status.draft), post(title="Alpha live", status=Status.published),
           post(title="Beta live", status=Status.published))
    page = admin_client.get("/admin/post?view=table&q=alpha&f_status=published").text
    assert "Alpha live" in page and "Alpha draft" not in page and "Beta live" not in page


######################################################################
# Sorting and paging
######################################################################
def test_default_order_is_newest_first(admin_client, db):
    db.add(post(title="First"), post(title="Second"))
    assert order_of(admin_client.get("/admin/post?view=table").text, "Second", "First")


@pytest.mark.parametrize("sort, expected", [("title", ("Apple", "Mango", "Zebra")), ("-title", ("Zebra", "Mango", "Apple")),
                                            ("views", ("Zebra", "Apple", "Mango"))])
def test_sort(admin_client, db, sort, expected):
    db.add(post(title="Mango", views=9), post(title="Apple", views=5), post(title="Zebra", views=1))
    assert order_of(admin_client.get(f"/admin/post?view=table&sort={sort}").text, *expected)


@pytest.mark.parametrize("sort", ["nope", "-", "author.name", "title;drop table posts"])
def test_unknown_sort_is_ignored(admin_client, db, sort):
    db.add(post(title="Still here"))
    r = admin_client.get("/admin/post?view=table", params={"sort": sort})
    assert r.status_code == HTTPStatus.OK and "Still here" in r.text


def test_ordering_option():
    from .server.app import AuthorAdmin

    class ByName(AuthorAdmin):
        ordering = "name"

    stmt = str(ByName(None).list_query())
    assert stmt.rstrip().endswith("ORDER BY authors.name ASC")


def test_paging(admin_client, db):
    db.add(*[author(name=f"Person {i:02}") for i in range(30)])
    first = admin_client.get("/admin/author?sort=name").text
    second = admin_client.get("/admin/author?sort=name&page=2").text
    assert "Person 00" in first and "Person 24" in first and "Person 25" not in first
    assert "Person 25" in second and "Person 29" in second and "Person 00" not in second
    assert "Page <b class=\"text-fg\">2</b> of <b class=\"text-fg\">2</b>" in second


@pytest.mark.parametrize("page", ["abc", "0", "-1", "", "1.5"])
def test_bad_page_falls_back_to_first(admin_client, db, page):
    db.add(author(name="Someone"))
    r = admin_client.get("/admin/author", params={"page": page})
    assert r.status_code == HTTPStatus.OK and "Someone" in r.text


def test_page_past_the_end_is_empty(admin_client, db):
    db.add(author(name="Someone"))
    r = admin_client.get("/admin/author?page=5")
    assert r.status_code == HTTPStatus.OK and "Someone" not in r.text


def test_board_shows_more_rows_per_page(admin_client, db):
    db.add(*[post(title=f"Card {i:03}") for i in range(60)])
    page = admin_client.get("/admin/post?view=board").text
    assert page.count("data-pk=") == 60


def test_models_without_str(admin_client, db):
    db.add(tag(code="X1"))
    assert admin_client.get("/admin/tag").status_code == HTTPStatus.OK
