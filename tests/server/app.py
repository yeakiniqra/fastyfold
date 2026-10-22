"""The admin the suite runs against: `create_app(engine)` returns a FastAPI app with it mounted."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, field_validator

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
    list_display = ["id", "title", "author", "status", "views", "published_at"]
    search_fields = ["title", "author.name"]
    list_filter = ["status", "published_at"]
    board_field = "status"
    colors = {"published": "#10b981"}
    schema = PostIn

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
    fieldsets = [("Profile", ["name", "email", "active"]), ("About", ["bio", "born"])]


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
    admin = Admin(engine, secret_key="test-secret", authenticate=authenticate, title="Test Site", **options)
    for cls in models:
        admin.register(cls)

    @asynccontextmanager
    async def lifespan(app):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield

    app = FastAPI(lifespan=lifespan)
    admin.mount(app, path)
    app.state.admin = admin
    return app
