import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from fastyfold import audit
from fastyfold.security import SESSION_COOKIE

from .server.app import create_app


def login(client, username="admin", password=None, **data):
    r = client.post("/admin/login", data={"username": username, "password": password or username, **data})
    assert r.status_code == 303, r.text
    return r


class DB:
    """Seed and inspect rows on the app's own event loop (aiosqlite connections are loop-bound)."""

    def __init__(self, run, admin):
        self.run, self.admin = run, admin

    def add(self, *objs):
        async def go():
            async with self.admin.sessionmaker() as s:
                s.add_all(objs)
                await s.commit()
        self.run(go)
        return objs[0] if len(objs) == 1 else objs

    def get(self, model, pk, *relations):
        """One row by primary key, with `relations` (names) loaded since async sessions can't lazy-load."""
        async def go():
            async with self.admin.sessionmaker() as s:
                return await s.get(model, pk, options=[selectinload(getattr(model, r)) for r in relations])
        return self.run(go)

    def count(self, model) -> int:
        async def go():
            async with self.admin.sessionmaker() as s:
                return await s.scalar(select(func.count()).select_from(model))
        return self.run(go)

    def log(self) -> list[audit.LogEntry]:
        """Every audit entry, newest first."""
        async def go():
            async with self.admin.session() as s:
                return await audit.recent(s, limit=1000)
        return self.run(go)


@pytest.fixture
def admin_options():
    """Extra `Admin(...)` keyword arguments. Parametrize this to change the site under test."""
    return {}


@pytest.fixture
def app(admin_options):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    return create_app(engine, **admin_options)


@pytest.fixture
def admin(app):
    return app.state.admin


@pytest.fixture
def client(app):
    with TestClient(app, follow_redirects=False) as c:
        yield c
        c.portal.call(app.state.admin.engine.dispose)


@pytest.fixture
def admin_client(client):
    login(client, "admin")
    return client


@pytest.fixture
def editor_client(client):
    login(client, "editor")
    return client


@pytest.fixture
def csrf(client, admin):
    """The current session's CSRF token."""
    return lambda: admin.signer.csrf(client.cookies.get(SESSION_COOKIE))


@pytest.fixture
def submit(client, csrf):
    """POST with a valid CSRF token, like the admin's own forms."""
    def go(url, data=None, **kw):
        return client.post(url, data={"_csrf": csrf(), **(data or {})}, **kw)
    return go


@pytest.fixture
def db(client, admin):
    return DB(client.portal.call, admin)
