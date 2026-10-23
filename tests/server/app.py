"""The admin the suite runs against: `create_app(engine)` returns a FastAPI app with it mounted."""

import tempfile
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from markupsafe import escape
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select

from fastyfold import Admin, ModelAdmin, action

from .models import Author, Base, Post, Secret, Status, Tag

USERS = {"admin": "admin", "editor": "editor"}


async def authenticate(username: str, password: str) -> str | None:
    return username if USERS.get(username) == password else None


class PostIn(BaseModel):
    title: str
    views: int

    @field_validator("title")
    @classmethod
    def no_spam(cls, v):
        if "spam" in v.lower():
            raise ValueError("No spam, please")
        return v


class PostAdmin(ModelAdmin):
    model = Post
    group = "Content"
    icon = "article"
    key = "p"
    list_display = ["id", "title", "author", "status", "views", "published_at", "reach"]
    search_fields = ["title", "author.name"]
    list_filter = ["status", "published_at", "author_id", "views"]
    board_field = "status"
    colors = {"published": "#10b981"}
    schema = PostIn
    inlines = [("comments", ["body", "author_id", "likes", "approved"])]

    def reach(self, obj):
        return f"{obj.views * 3:,} people"

    def __init__(self, admin):
        super().__init__(admin)
        self.saved: list[tuple[str, bool]] = []

    async def save(self, session, obj, is_new):
        self.saved.append((obj.title, is_new))
        session.add(obj)

    @action("Publish", icon="publish", key="u")
    async def publish(self, session, posts):
        for p in posts:
            p.status = Status.published

    @action("Archive", icon="archive", confirm="Archive the selected posts?")
    async def archive(self, session, posts):
        for p in posts:
            p.status = Status.archived

    def has_permission(self, user, perm):
        return perm != "archive" or user == "admin"


class AuthorAdmin(ModelAdmin):
    model = Author
    group = "People"
    icon = "person"
    key = "a"
    fieldsets = [("Profile", ["name", "email", "active"]), ("About", ["bio", "born"]), ("Files", ["photo", "cv"])]
    file_fields = {"photo": "image", "cv": "file"}


class TagAdmin(ModelAdmin):
    model = Tag
    group = "Content"
    icon = "sell"
    readonly_fields = ["code"]
    choices = {"kind": ["topic", ("series", "Series")]}


class SecretAdmin(ModelAdmin):
    model = Secret
    group = "Access"
    icon = "lock"

    def has_permission(self, user, perm):
        return user == "admin"


MODEL_ADMINS = (PostAdmin, AuthorAdmin, TagAdmin, SecretAdmin)


def create_app(engine, *, path: str = "/admin", models=MODEL_ADMINS, **options) -> FastAPI:
    options.setdefault("media_dir", tempfile.mkdtemp())
    admin = Admin(engine, secret_key="test-secret", authenticate=authenticate, title="Test Site", **options)
    for cls in models:
        admin.register(cls)

    @admin.page("Reports", icon="bar_chart", group="Insights", key="r")
    async def reports(request, user):
        if request.method == "POST":
            form = await request.form()
            return f"<p id='echo'>Got {escape(form.get('note', ''))}</p>"
        csrf = admin.csrf(request)
        return (f"<p id='hello'>Hello {escape(user)}</p><form method='post'><input type='hidden' name='_csrf' value='{csrf}'>"
                "<input name='note'><button>Send</button></form>")

    @admin.page("Raw", group="Insights")
    async def raw(request, user):
        return JSONResponse({"user": user})

    @admin.widget("Total views", icon="visibility")
    async def total_views(session, user):
        return {"value": await session.scalar(select(func.coalesce(func.sum(Post.views), 0))), "caption": "across all posts"}

    @admin.widget("Authors", span=4)
    async def authors_count(session, user):
        return await session.scalar(select(func.count()).select_from(Author))

    @admin.widget("Views by status", span=6)
    async def by_status(session, user):
        rows = await session.execute(select(Post.status, func.sum(Post.views)).group_by(Post.status))
        return [(s.value.title(), n) for s, n in rows]

    @admin.widget("Owner only")
    async def owner_only(session, user):
        return "Only the owner sees this" if user == "admin" else None

    @asynccontextmanager
    async def lifespan(app):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield

    app = FastAPI(lifespan=lifespan)
    admin.mount(app, path)
    app.state.admin = admin
    return app
