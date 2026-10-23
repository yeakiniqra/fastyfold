"""Browser tests for what only runs in JavaScript: theme, ⌘K, shortcuts, bulk bar, split view, board drag and drop.

Needs `playwright install chromium` once.
"""

import asyncio
import re
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright")

import uvicorn  # noqa: E402
from playwright.sync_api import expect  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from fastyfold.security import SESSION_COOKIE  # noqa: E402

from .factories import PNG, author, comment, post, tag  # noqa: E402
from .fixtures import DB  # noqa: E402
from .server.app import create_app  # noqa: E402
from .server.models import Author, Post, Status  # noqa: E402


@pytest.fixture
def live(admin_options):
    """The test site served by uvicorn on a free port, in a background thread."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    app = create_app(engine, **admin_options)
    server = uvicorn.Server(uvicorn.Config(app, port=0, log_level="warning"))
    loop = asyncio.new_event_loop()

    async def serve():
        await server.serve()
        await engine.dispose()

    thread = threading.Thread(target=loop.run_until_complete, args=(serve(),), daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    run = lambda fn, *args: asyncio.run_coroutine_threadsafe(fn(*args), loop).result()  # noqa: E731
    yield SimpleNamespace(url=f"http://127.0.0.1:{port}", admin=app.state.admin, db=DB(run, app.state.admin))
    server.should_exit = True
    thread.join()
    loop.close()


@pytest.fixture
def page(page, live):
    """A signed-in admin page. External fonts are blocked so tests never wait on the network."""
    page.route(re.compile(r"fonts\.(googleapis|gstatic)\.com"), lambda route: route.abort())
    page.context.add_cookies([{"name": SESSION_COOKIE, "value": live.admin.signer.dump("admin"), "url": live.url}])
    page.set_default_timeout(5000)
    return page


@pytest.fixture
def go(page, live):
    def visit(path):
        page.goto(live.url + path)
        page.wait_for_function("() => window.Alpine && window.htmx && document.body._x_dataStack")
    return visit


######################################################################
# Sign in and out
######################################################################
def test_sign_in_form(page, live):
    page.context.clear_cookies()
    page.goto(live.url + "/admin/post?view=table")
    expect(page).to_have_url(re.compile(r"/admin/login\?next="))
    page.fill("#username", "admin")
    page.fill("#password", "admin")
    page.press("#password", "Enter")
    expect(page).to_have_url(live.url + "/admin/post")
    expect(page.locator("h1")).to_have_text("Posts")


def test_sign_out(page, go):
    go("/admin/")
    page.click('button[title="Sign out admin"]')
    expect(page).to_have_url(re.compile(r"/admin/login$"))


######################################################################
# Theme
######################################################################
def test_theme_buttons_persist(page, go):
    go("/admin/")
    html = page.locator("html")
    page.click('button[title="Dark theme"]')
    expect(html).to_have_class(re.compile(r"\bdark\b"))
    assert page.evaluate("localStorage.getItem('ff-theme')") == "dark"
    go("/admin/post")
    expect(html).to_have_class(re.compile(r"\bdark\b"))
    page.click('button[title="Light theme"]')
    expect(html).not_to_have_class(re.compile(r"\bdark\b"))


@pytest.mark.parametrize("scheme, dark", [("dark", True), ("light", False)])
def test_system_theme_follows_os(page, go, scheme, dark):
    page.emulate_media(color_scheme=scheme)
    go("/admin/")
    page.click('button[title="System theme"]')
    assert page.evaluate("document.documentElement.classList.contains('dark')") is dark


def test_t_toggles_theme(page, go):
    page.emulate_media(color_scheme="light")
    go("/admin/")
    page.keyboard.press("t")
    expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
    page.keyboard.press("t")
    expect(page.locator("html")).not_to_have_class(re.compile(r"\bdark\b"))


######################################################################
# ⌘K palette
######################################################################
def palette(page):
    return page.locator("[x-ref=pq]")


def open_palette(page):
    page.keyboard.press("Control+k")
    expect(palette(page)).to_be_focused()


def test_palette_opens_with_ctrl_k_and_slash(page, go):
    go("/admin/")
    page.keyboard.press("Control+k")
    expect(palette(page)).to_be_visible()
    expect(palette(page)).to_be_focused()
    page.keyboard.press("Escape")
    expect(palette(page)).to_be_hidden()
    page.keyboard.press("/")
    expect(palette(page)).to_be_visible()
    page.keyboard.press("Control+k")
    expect(palette(page)).to_be_hidden()


def test_palette_search_highlights_and_opens_record(page, go, live):
    live.db.add(post(title="Jamdani scarf"))
    go("/admin/")
    page.click("text=Search records, models, actions…")
    expect(palette(page)).to_be_focused()
    page.keyboard.type("jamd")
    expect(page.locator("mark", has_text="Jamd")).to_be_visible()
    page.keyboard.press("Enter")
    expect(page).to_have_url(live.url + "/admin/post/1")


def test_palette_arrow_keys(page, go, live):
    go("/admin/")
    open_palette(page)
    page.keyboard.type("authors")
    expect(page.get_by_role("button", name=re.compile("Go to authors"))).to_be_visible()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowUp")
    page.keyboard.press("Enter")
    expect(page).to_have_url(live.url + "/admin/author")


def test_palette_commands(page, go):
    page.emulate_media(color_scheme="light")
    go("/admin/")
    open_palette(page)
    page.keyboard.type("dark mode")
    page.keyboard.press("Enter")
    expect(page.locator("html")).to_have_class(re.compile(r"\bdark\b"))
    open_palette(page)
    page.keyboard.type("shortcuts")
    page.keyboard.press("Enter")
    expect(page.get_by_role("heading", name="Keyboard shortcuts")).to_be_visible()


def test_palette_enter_waits_for_fresh_results(page, go, live):
    """Typing then Enter inside the 150 ms debounce must run the new query's top hit, not the old one's."""
    go("/admin/_log")
    open_palette(page)
    expect(page.get_by_role("button", name=re.compile("Go to posts"))).to_be_visible()
    page.keyboard.type("new author", delay=0)
    page.keyboard.press("Enter")
    expect(page).to_have_url(live.url + "/admin/author/new")


def test_palette_no_matches(page, go):
    go("/admin/")
    open_palette(page)
    page.keyboard.type("zzzz")
    expect(page.get_by_text("No matches for")).to_be_visible()


######################################################################
# Global keys
######################################################################
def test_shortcut_sheet(page, go):
    go("/admin/")
    sheet = page.get_by_role("heading", name="Keyboard shortcuts")
    page.keyboard.press("?")
    expect(sheet).to_be_visible()
    expect(page.get_by_text("Go to posts")).to_be_visible()
    page.keyboard.press("Escape")
    expect(sheet).to_be_hidden()


@pytest.mark.parametrize("key, path", [("p", "/admin/post"), ("a", "/admin/author"), ("d", "/admin/")])
def test_go_to(page, go, live, key, path):
    go("/admin/_log")
    page.keyboard.press("g")
    page.keyboard.press(key)
    expect(page).to_have_url(live.url + path)


def test_n_opens_new_form(page, go, live):
    go("/admin/author")
    page.keyboard.press("n")
    expect(page).to_have_url(live.url + "/admin/author/new")


def test_keys_are_ignored_while_typing(page, go, live):
    go("/admin/author")
    search = page.get_by_placeholder("Search authors…")
    search.click()
    page.keyboard.type("gnat")
    expect(search).to_have_value("gnat")
    expect(page).to_have_url(live.url + "/admin/author")


def test_escape_goes_back_from_form(page, go, live):
    a = live.db.add(author())
    go(f"/admin/author/{a.id}")
    page.keyboard.press("Escape")
    expect(page).to_have_url(live.url + "/admin/author")


def test_toast_shows_then_hides(page, go):
    go("/admin/author?msg=Saved+Someone")
    toast = page.get_by_text("Saved Someone")
    expect(toast).to_be_visible()
    expect(toast).to_be_hidden(timeout=6000)


######################################################################
# Table: cursor, selection, bulk bar
######################################################################
@pytest.fixture
def three_posts(live):
    return live.db.add(post(title="Oldest"), post(title="Middle"), post(title="Newest"))


def active(page):
    return page.locator("[data-row][data-active]")


def test_j_k_move_and_enter_opens(page, go, live, three_posts):
    go("/admin/post?view=table")
    page.keyboard.press("j")
    expect(active(page)).to_contain_text("Newest")
    page.keyboard.press("j")
    page.keyboard.press("j")
    page.keyboard.press("j")
    expect(active(page)).to_contain_text("Oldest")
    page.keyboard.press("k")
    expect(active(page)).to_contain_text("Middle")
    page.keyboard.press("Enter")
    expect(page).to_have_url(live.url + "/admin/post/2")


def test_e_opens_active_row(page, go, live, three_posts):
    go("/admin/post?view=table")
    page.keyboard.press("j")
    page.keyboard.press("e")
    expect(page).to_have_url(live.url + "/admin/post/3")


def test_clicking_a_row_opens_it(page, go, live, three_posts):
    go("/admin/post?view=table")
    page.locator("tr[data-row]", has_text="Middle").locator("td").last.click()
    expect(page).to_have_url(live.url + "/admin/post/2")


def bulk_bar(page):
    return page.get_by_text(re.compile(r"^\d+ selected$"))


def test_x_selects_and_bulk_bar_counts(page, go, three_posts):
    go("/admin/post?view=table")
    expect(bulk_bar(page)).to_be_hidden()
    page.keyboard.press("j")
    page.keyboard.press("x")
    expect(bulk_bar(page)).to_have_text("1 selected")
    page.get_by_label("Select all").check()
    expect(bulk_bar(page)).to_have_text("3 selected")
    page.get_by_label("Clear selection").click()
    expect(bulk_bar(page)).to_be_hidden()


def test_bulk_action(page, go, live, three_posts):
    go("/admin/post?view=table")
    page.get_by_label("Select Oldest").check()
    page.get_by_label("Select Newest").check()
    page.locator("button[value=publish]").click()
    expect(page.get_by_text("Publish: 2 posts")).to_be_visible()
    statuses = [live.db.get(Post, pk).status for pk in (1, 2, 3)]
    assert statuses == [Status.published, Status.draft, Status.published]


@pytest.mark.parametrize("accept, expected", [(False, Status.draft), (True, Status.archived)])
def test_bulk_action_confirm(page, go, live, three_posts, accept, expected):
    go("/admin/post?view=table")
    page.on("dialog", lambda d: d.accept() if accept else d.dismiss())
    page.get_by_label("Select Middle").check()
    page.locator("button[value=archive]").click()
    if accept:
        expect(page.get_by_text("Archive: 1 posts")).to_be_visible()
    else:
        page.wait_for_timeout(300)
    assert live.db.get(Post, 2).status == expected


def test_bulk_delete_confirm(page, go, live, three_posts):
    go("/admin/post?view=table")
    page.on("dialog", lambda d: d.accept())
    page.get_by_label("Select Middle").check()
    page.locator("button[value=delete]").click()
    expect(page.get_by_text("Deleted: 1 posts")).to_be_visible()
    assert live.db.get(Post, 2) is None


def test_action_key_runs_on_selection(page, go, live, three_posts):
    go("/admin/post?view=table")
    page.keyboard.press("u")
    page.wait_for_timeout(200)
    assert live.db.get(Post, 3).status == Status.draft, "nothing selected, the key does nothing"
    page.keyboard.press("j")
    page.keyboard.press("x")
    expect(page.locator("[data-action-key=u]").first).to_be_visible()
    page.keyboard.press("u")
    expect(page.get_by_text("Publish: 1 posts")).to_be_visible()
    assert live.db.get(Post, 3).status == Status.published


@pytest.mark.parametrize("key, view", [("1", "table"), ("2", "board"), ("3", "split")])
def test_view_keys(page, go, live, key, view):
    go("/admin/post?view=table" if key != "1" else "/admin/post?view=board")
    page.keyboard.press(key)
    expect(page).to_have_url(re.compile(rf"view={view}"))


######################################################################
# Filter panel
######################################################################
def test_filter_panel(page, go, live):
    live.db.add(post(title="Drafty"), post(title="Live one", status=Status.published))
    go("/admin/post?view=table")
    page.keyboard.press("f")
    panel = page.get_by_role("heading", name="Filters")
    expect(panel).to_be_visible()
    page.locator("fieldset", has_text="Status").get_by_text("Published", exact=True).click()
    page.get_by_role("button", name="Apply filters").click()
    expect(page).to_have_url(re.compile(r"f_status=published"))
    expect(page.get_by_text("Live one")).to_be_visible()
    expect(page.get_by_role("cell", name="Drafty")).to_have_count(0)


def test_filter_panel_closes_with_escape(page, go):
    go("/admin/post?view=table")
    page.get_by_role("button", name=re.compile("Filter")).click()
    expect(page.get_by_role("heading", name="Filters")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("heading", name="Filters")).to_be_hidden()


######################################################################
# Split view
######################################################################
def test_split_preview_loads_and_follows_cursor(page, go, live, three_posts):
    sent = []
    page.on("request", lambda r: "/preview" in r.url and sent.append(r.headers))
    go("/admin/post?view=split")
    preview = page.locator("#preview")
    expect(preview.get_by_role("heading", name="Newest")).to_be_visible()
    page.keyboard.press("j")
    page.keyboard.press("j")
    expect(preview.get_by_role("heading", name="Middle")).to_be_visible()
    page.get_by_role("button", name=re.compile("Oldest")).click()
    expect(preview.get_by_role("heading", name="Oldest")).to_be_visible()
    assert sent and all(h.get("hx-request") == "true" and len(h.get("x-csrf-token", "")) == 64 for h in sent)


def test_split_preview_action_key(page, go, live, three_posts):
    go("/admin/post?view=split")
    expect(page.locator("#preview").get_by_role("heading", name="Newest")).to_be_visible()
    page.keyboard.press("u")
    expect(page.get_by_text("Publish: 1 posts")).to_be_visible()
    expect(page).to_have_url(re.compile(r"view=split"))
    assert live.db.get(Post, 3).status == Status.published


def test_split_enter_opens(page, go, live, three_posts):
    go("/admin/post?view=split")
    page.keyboard.press("j")
    page.keyboard.press("Enter")
    expect(page).to_have_url(live.url + "/admin/post/3")


######################################################################
# Board
######################################################################
def column(page, value):
    return page.locator(f'[data-column="{value}"]')


def test_drag_card_to_column(page, go, live):
    p = live.db.add(post(title="Drag me"))
    go("/admin/post?view=board")
    # Wait for the response rather than polling: the test DB shares one connection with the server.
    with page.expect_response(lambda r: r.url.endswith("/move")) as moved:
        page.locator(f'[data-pk="{p.id}"]').drag_to(column(page, "published").locator("[data-drop]"))
    assert moved.value.status == 204
    expect(column(page, "published").locator(f'[data-pk="{p.id}"]')).to_be_visible()
    assert live.db.get(Post, p.id).status == Status.published


def test_failed_drag_puts_card_back(page, go, live):
    p = live.db.add(post(title="Stuck"))
    page.route("**/move", lambda route: route.fulfill(status=500))
    alerts = []
    page.on("dialog", lambda d: (alerts.append(d.message), d.accept()))
    go("/admin/post?view=board")
    page.locator(f'[data-pk="{p.id}"]').drag_to(column(page, "review").locator("[data-drop]"))
    expect(column(page, "draft").locator(f'[data-pk="{p.id}"]')).to_be_visible()
    assert alerts == ["Could not move this card. Reload and try again."]
    assert live.db.get(Post, p.id).status == Status.draft


######################################################################
# Forms
######################################################################
def test_ctrl_s_saves_while_typing(page, go, live):
    a = live.db.add(author(name="Before"))
    go(f"/admin/author/{a.id}")
    page.fill("#f-name", "After")
    page.keyboard.press("Control+s")
    expect(page).to_have_url(re.compile(r"/admin/author\?msg="))
    assert live.db.get(Author, a.id).name == "After"


def test_ctrl_enter_saves_and_keeps_editing(page, go, live):
    a = live.db.add(author(name="Before"))
    go(f"/admin/author/{a.id}")
    page.fill("#f-name", "Kept open")
    page.keyboard.press("Control+Enter")
    expect(page).to_have_url(re.compile(rf"/admin/author/{a.id}\?msg="))
    expect(page.locator("#f-name")).to_have_value("Kept open")


def test_bool_toggle(page, go, live):
    a = live.db.add(author(name="Toggle", active=True))
    go(f"/admin/author/{a.id}")
    toggle = page.locator("label", has=page.locator("#f-active"))
    expect(toggle).to_contain_text("Yes")
    toggle.click()
    expect(toggle).to_contain_text("No")
    page.keyboard.press("Control+s")
    expect(page).to_have_url(re.compile(r"\?msg="))
    assert live.db.get(Author, a.id).active is False


@pytest.mark.parametrize("accept", [False, True])
def test_delete_from_form(page, go, live, accept):
    p = live.db.add(post(title="Maybe"))
    page.on("dialog", lambda d: d.accept() if accept else d.dismiss())
    go(f"/admin/post/{p.id}")
    page.get_by_role("button", name=re.compile("Delete")).click()
    if accept:
        expect(page.get_by_text("Deleted Maybe")).to_be_visible()
        assert live.db.get(Post, p.id) is None
    else:
        page.wait_for_timeout(300)
        assert live.db.get(Post, p.id) is not None


@pytest.mark.parametrize("where", ["form", "preview"])
def test_delete_confirm_cannot_be_escaped(page, go, live, where):
    title = "x'); window.pwned = 1; ('\"</b>"
    p = live.db.add(post(title=title))
    messages = []
    page.on("dialog", lambda d: (messages.append(d.message), d.dismiss()))
    if where == "form":
        go(f"/admin/post/{p.id}")
        page.get_by_role("button", name="Delete").click()
    else:
        go("/admin/post?view=split")
        page.locator("#preview button[value=delete]").click()
    expect(page.locator("body")).to_be_visible()
    assert messages and messages[0].startswith(f"Delete {title}?")
    assert page.evaluate("window.pwned") is None
    assert live.db.get(Post, p.id) is not None


def test_validation_errors_are_shown(page, go, live):
    go("/admin/author/new")
    page.keyboard.press("Control+s")
    expect(page.get_by_text("This field is required.")).to_be_visible()
    expect(page.get_by_text("1 field needs attention")).to_be_visible()


######################################################################
# Autocomplete (fk and m2m)
######################################################################
def test_fk_autocomplete(page, go, live):
    live.db.add(author(name="Nusrat Jahan"), author(name="Arif Hossain"))
    go("/admin/post/new")
    page.click("#f-author_id")
    search = page.get_by_placeholder("Search…").first
    expect(search).to_be_focused()
    expect(page.get_by_role("option")).to_have_count(2)
    search.type("arif")
    expect(page.get_by_role("option")).to_have_count(1)
    page.keyboard.press("Enter")
    expect(page.locator("#f-author_id")).to_have_text("Arif Hossain")
    expect(page.locator('input[type=hidden][name="author_id"]')).to_have_value("2")
    page.fill("#f-title", "Picked")
    page.keyboard.press("Control+s")
    expect(page).to_have_url(re.compile(r"/admin/post\?msg="))
    assert live.db.get(Post, 1).author_id == 2


def test_autocomplete_escape_and_arrows(page, go, live):
    live.db.add(author(name="Alpha"), author(name="Beta"))
    go("/admin/post/new")
    page.click("#f-author_id")
    expect(page.get_by_role("option")).to_have_count(2)
    expect(page.get_by_placeholder("Search…").first).to_be_focused()
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    expect(page.locator("#f-author_id")).to_have_text("Alpha")
    page.click("#f-author_id")
    page.keyboard.press("Escape")   # pressed before focus reaches the search box, too
    expect(page.get_by_placeholder("Search…").first).to_be_hidden()
    page.click("#f-author_id")
    expect(page.get_by_placeholder("Search…").first).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#f-author_id")).to_be_focused()
    expect(page).to_have_url(live.url + "/admin/post/new")   # Escape closes the menu, it does not leave the form


def test_autocomplete_keys_reach_menu_from_button(page, go, live):
    """Keys that land on the button while the menu is open (fast typists) still drive the menu."""
    live.db.add(author(name="Alpha"), author(name="Beta"))
    go("/admin/post/new")
    button = page.locator("#f-author_id")
    button.click()
    expect(page.get_by_role("option")).to_have_count(2)
    button.press("ArrowDown")
    button.press("Enter")
    expect(button).to_have_text("Alpha")
    expect(page).to_have_url(live.url + "/admin/post/new")


def test_m2m_chips(page, go, live):
    live.db.add(tag(), tag(), tag())
    p = live.db.add(post(title="Tagged"))
    go(f"/admin/post/{p.id}")
    chips = page.locator("[data-chip]")
    page.get_by_role("button", name="Tags").click()   # labelled by its <label>
    page.get_by_role("option", name="Tag #1").click()
    page.get_by_role("option", name="Tag #3").click()
    expect(chips).to_have_count(2)
    page.get_by_placeholder("Search…").first.press("Backspace")
    expect(chips).to_have_count(1)
    page.get_by_role("option", name="Tag #2").click()
    page.get_by_role("button", name="Remove Tag #1").click()
    expect(chips).to_have_count(1)
    expect(chips.first).to_contain_text("Tag #2")
    page.keyboard.press("Control+s")
    expect(page).to_have_url(re.compile(r"\?msg="))
    assert [t.id for t in live.db.get(Post, p.id, "tags").tags] == [2]


######################################################################
# Inlines
######################################################################
def test_inline_rows(page, go, live):
    a = live.db.add(author(name="Commenter"))
    p = live.db.add(post(title="Parent", comments=[comment(body="Keep"), comment(body="Drop")]))
    go(f"/admin/post/{p.id}")
    section = page.locator("#inline-comments")
    rows = section.locator("[data-inline-row]")
    expect(rows).to_have_count(2)
    section.get_by_role("button", name="Add row").click()
    section.get_by_role("button", name="Add row").click()
    expect(rows).to_have_count(4)
    rows.nth(3).get_by_role("button", name="Remove row").click()
    expect(rows).to_have_count(3)
    new = rows.nth(2)
    new.get_by_label("Body").fill("Fresh")
    new.get_by_label("Likes").fill("4")
    new.get_by_role("button", name="Select…").click()
    page.get_by_role("option", name="Commenter").click()
    rows.nth(1).get_by_title("Remove row").click()
    expect(rows.nth(1)).to_have_class(re.compile("opacity-40"))
    page.keyboard.press("Control+s")
    expect(page).to_have_url(re.compile(r"\?msg="))
    saved = sorted((c.body, c.likes, c.author_id) for c in live.db.get(Post, p.id, "comments").comments)
    assert saved == [("Fresh", 4, a.id), ("Keep", 0, None)]


######################################################################
# Filters, actions, pages, uploads, export
######################################################################
def test_fk_and_range_filters(page, go, live):
    nusrat = live.db.add(author(name="Nusrat"))
    live.db.add(post(title="Hers", author=nusrat, views=5), post(title="Other", views=5), post(title="Too many", author=nusrat, views=50))
    go("/admin/post?view=table")
    page.keyboard.press("f")
    panel = page.locator("form", has=page.get_by_role("heading", name="Filters"))
    panel.locator("fieldset", has_text="Author").get_by_role("button", name="Select…").click()
    page.get_by_role("option", name="Nusrat").click()
    panel.get_by_label("Views from").fill("1")
    panel.get_by_label("Views to").fill("10")
    panel.get_by_role("button", name="Apply filters").click()
    expect(page).to_have_url(re.compile(rf"f_author_id={nusrat.id}.*f_views=1\.\.10"))
    expect(page.get_by_text("Hers", exact=True)).to_have_count(1)
    expect(page.get_by_text("Other", exact=True)).to_have_count(0)
    expect(page.get_by_text("Views: 1 – 10")).to_be_visible()


def test_record_action_key_on_form(page, go, live):
    p = live.db.add(post())
    go(f"/admin/post/{p.id}")
    page.keyboard.press("u")
    expect(page).to_have_url(re.compile(rf"/admin/post/{p.id}\?msg=Publish"))
    assert live.db.get(Post, p.id).status == Status.published


def test_custom_page_form(page, go):
    go("/admin/reports")
    expect(page.locator("#hello")).to_have_text("Hello admin")
    page.fill("input[name=note]", "from the browser")
    page.click("text=Send")
    expect(page.locator("#echo")).to_have_text("Got from the browser")


def test_image_upload(page, go, live, tmp_path):
    a = live.db.add(author(name="Pictured"))
    (tmp_path / "me.png").write_bytes(PNG)
    go(f"/admin/author/{a.id}")
    page.set_input_files("#f-photo", str(tmp_path / "me.png"))
    page.keyboard.press("Control+Enter")
    expect(page.get_by_alt_text("Current photo")).to_be_visible()
    src = page.get_by_alt_text("Current photo").get_attribute("src")
    assert page.request.get(live.url + src).body() == PNG


def test_export_download(page, go, live):
    live.db.add(post(title="Exported"))
    go("/admin/post?view=table")
    with page.expect_download() as dl:
        page.get_by_role("link", name="Export").click()
    assert dl.value.suggested_filename == "post.csv"
    assert "Exported" in open(dl.value.path(), encoding="utf-8").read()
