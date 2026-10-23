import pytest

from fastyfold import ModelAdmin, action
from fastyfold.model import PALETTE, _plural, _snake

from .factories import author, post, tag
from .server.app import AuthorAdmin, PostAdmin, TagAdmin
from .server.models import Author, Status


@pytest.fixture
def posts():
    return PostAdmin(None)


@pytest.fixture
def authors():
    return AuthorAdmin(None)


######################################################################
# Names and defaults
######################################################################
def test_names_from_class_name(posts):
    assert (posts.name, posts.name_plural, posts.slug, posts.pk) == ("Post", "Posts", "post", "id")


@pytest.mark.parametrize("word, plural", [("Post", "Posts"), ("Category", "Categories"), ("Day", "Days"),
                                          ("Box", "Boxes"), ("Status", "Statuses"), ("Match", "Matches")])
def test_plural(word, plural):
    assert _plural(word) == plural


def test_snake():
    assert _snake("BlogPost") == "blog_post" and _snake("Post") == "post"


def test_explicit_names_win():
    class A(ModelAdmin):
        model = Author
        name, name_plural, slug = "Writer", "Writers", "writers"

    a = A(None)
    assert (a.name, a.name_plural, a.slug) == ("Writer", "Writers", "writers")


def test_defaults(authors):
    assert authors.list_display == ["id", "name", "email", "bio", "active", "born"]
    assert authors.search_fields == ["name", "email"]
    assert authors.list_filter == ["active", "born", "joined"]
    assert authors.form_fields == ["name", "email", "bio", "active", "born", "photo", "cv"], "pk and server-default columns stay off the form"
    assert authors.preview_fields == authors.list_display


def test_default_fieldset_leaves_board_field_to_its_own_card(posts):
    assert posts.fieldsets == [("Details", ["title", "author_id", "views", "rating", "meta", "published_at", "summary", "tags"])]


def test_readonly_fields_stay_off_the_form():
    tags = TagAdmin(None)
    assert tags.fields["code"].readonly and "code" not in tags.form_fields


def test_empty_search_fields_are_respected():
    class A(ModelAdmin):
        model = Author
        search_fields = []
        list_filter = []

    a = A(None)
    assert a.search_fields == [] and a.list_filter == []


######################################################################
# Row identity
######################################################################
def test_identity_fields(posts, authors):
    assert (posts.title_field, posts.subtitle_field, posts.lead_fk) == ("title", None, "author")
    assert (authors.title_field, authors.subtitle_field, authors.lead_fk) == ("name", "email", None)


def test_identity_helpers(posts, authors):
    p = post(title="Hello", author=author(name="Nusrat Jahan"))
    assert posts.avatar_text(p) == "Hello" and posts.subtitle(p) == ""
    a = author(name="Nusrat Jahan", email="n@example.com")
    assert authors.avatar_text(a) == "Nusrat Jahan" and authors.subtitle(a) == "n@example.com"


def test_lead_fk_gives_records_without_text_a_face():
    class Orders(ModelAdmin):
        model = PostAdmin.model
        list_display = ["id", "author", "views"]

    o = Orders(None)
    p = post(id=7, author=author(name="Arif"))
    assert o.title_field is None and o.lead_fk == "author"
    assert o.avatar_text(p) == "Arif" and o.subtitle(p) == "Arif"


def test_label_falls_back_for_models_without_str():
    tags = TagAdmin(None)
    assert tags.label(tag(id=4)) == "Tag #4"
    assert PostAdmin(None).label(post(title="Hi")) == "Hi"


def test_card_fields_put_numbers_first():
    class P(PostAdmin):
        list_display = ["id", "title", "status", "published_at", "rating", "views"]

    assert P(None).card_fields() == ["rating", "published_at", "views"]


def test_column_label(posts):
    assert posts.column_label("id") == "ID"
    assert posts.column_label("author_id") == "Author"
    assert posts.column_label("author") == "Author"
    assert posts.column_label("author.name") == "Author name"


######################################################################
# Values
######################################################################
def test_value_follows_dotted_names(posts):
    p = post(author=author(name="Mitu"))
    assert posts.value(p, "author.name") == "Mitu"
    assert posts.value(post(author=None, author_id=1), "author.name") is None


def test_display(posts):
    p = post(title="T", status=Status.review, views=3, author=author(name="Sofia"))
    assert posts.display(p, "status") == "Review"
    assert posts.display(p, "author_id") == "Sofia", "fk columns show the related row"
    assert posts.display(p, "author") == "Sofia"
    assert posts.display(p, "author.name") == "Sofia"
    assert posts.display(post(rating=None), "rating") == "—"


def test_raw_unwraps_enums(posts):
    assert posts.raw(post(status=Status.review), "status") == "review"


def test_color(posts):
    assert posts.color("status", Status.published) == "#10b981", "explicit colours win"
    assert posts.color("status", "draft") == PALETTE[0]
    assert posts.color("status", "review") == PALETTE[1]
    assert posts.color("status", "nope") == PALETTE[3]


def test_is_badge(posts):
    assert posts.is_badge("status") and not posts.is_badge("title") and not posts.is_badge("author")


def test_filter_options(authors):
    opts = {o["name"]: o for o in authors.filter_options()}
    assert opts["active"]["options"] == [("1", "Yes"), ("0", "No")]
    assert opts["born"]["kind"] == "date" and opts["joined"]["kind"] == "datetime"
    assert [v for v, _ in opts["joined"]["options"]] == ["today", "7d", "30d"]


######################################################################
# Actions
######################################################################
def test_action_decorator_keeps_function():
    @action("Do it", icon="bolt", key="d", confirm="Sure?")
    async def fn(self, session, objs):
        pass

    assert fn._ff_action == {"label": "Do it", "icon": "bolt", "key": "d", "confirm": "Sure?"}


def test_actions_are_discovered(posts):
    assert set(posts.actions) == {"publish", "archive"}
    assert posts.actions["publish"] == {"label": "Publish", "icon": "publish", "key": "u", "confirm": None, "name": "publish"}


def test_actions_are_inherited():
    class More(PostAdmin):
        @action("Feature")
        async def feature(self, session, posts):
            pass

    assert set(More(None).actions) == {"publish", "archive", "feature"}


######################################################################
# Computed columns, export values, filter chips, lookups
######################################################################
def test_method_columns(posts):
    p = post(views=1200)
    assert posts.is_method("reach") and posts.display(p, "reach") == "3,600 people"
    assert posts.column_label("reach") == "Reach" and not posts.sortable("reach")
    assert not posts.is_method("label") and not posts.is_method("display"), "ModelAdmin's own methods are not columns"
    assert not posts.is_method("title")


def test_method_column_returning_none(posts):
    class P(PostAdmin):
        def nothing(self, obj):
            return None

    assert P(None).display(post(), "nothing") == "—"


def test_model_properties_work_as_columns():
    class A(AuthorAdmin):
        list_display = ["id", "name", "__class__.__name__"]

    assert A(None).display(author(), "__class__.__name__") == "Author"


def test_export_value(posts):
    from datetime import datetime
    p = post(title="=cmd", views=-2, status=Status.review, published_at=datetime(2026, 1, 2), meta={"a": 1}, author=author(name="+X"))
    assert posts.export_value(p, "title") == "'=cmd"
    assert posts.export_value(p, "views") == "-2"
    assert posts.export_value(p, "status") == "Review"
    assert posts.export_value(p, "published_at") == "2026-01-02T00:00:00"
    assert posts.export_value(p, "meta") == '{"a": 1}'
    assert posts.export_value(p, "author") == "'+X"
    assert posts.export_value(post(rating=None), "rating") == ""


def test_filter_text(posts):
    assert posts.filter_text("status", "review", {}) == "Review"
    assert posts.filter_text("author_id", "3", {"3": "Nusrat"}) == "Nusrat"
    assert posts.filter_text("views", "1..9", {}) == "1 – 9"
    assert posts.filter_text("views", "1..", {}) == "≥ 1"
    assert posts.filter_text("views", "..9", {}) == "≤ 9"
    assert posts.filter_text("published_at", "7d", {}) == "Last 7 days"


def test_lookup_field(posts):
    assert posts.lookup_field("author_id").name == "author_id"
    assert posts.lookup_field("tags").kind == "m2m"
    assert posts.lookup_field("comments.author_id").target is Author
    assert posts.lookup_field("title") is None and posts.lookup_field("comments.body") is None


def test_inline_spec(posts):
    (inl,) = posts.inlines
    assert (inl.name, inl.label, inl.pk, inl.names) == ("comments", "Comments", "id", ["body", "author_id", "likes", "approved"])
    assert inl.relations == ["author"], "the link back to the parent is not reloaded"


def test_inline_defaults():
    class P(PostAdmin):
        inlines = ["comments"]

    assert P(None).inlines[0].names == ["author_id", "body", "likes", "approved"]


def test_file_fields_as_list():
    class A(AuthorAdmin):
        file_fields = ["cv"]

    assert A(None).fields["cv"].kind == "file"
