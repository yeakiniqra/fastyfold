"""Admin: model registry, auth, and every HTTP view."""

import csv
import io
import mimetypes
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PrefixLoader
from markupsafe import Markup
from pydantic import ValidationError
from sqlalchemy import delete as sa_delete
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from starlette.datastructures import QueryParams

from . import audit, roles
from .audit import AdminSession
from .fields import Field, FieldError, fields_for
from .model import DAYS, Inline, ModelAdmin, call_hook, db_message
from .security import SESSION_COOKIE, SESSION_MAX_AGE, SESSION_SHORT_AGE, Signer, new_session_id
from .storage import IMAGE_TYPES, LocalStorage, check_upload, extension

HERE = Path(__file__).parent
LOGIN_WINDOW = 15 * 60
# Sent with every admin response: no framing (clickjacking), no caching of record data, no sniffing, no leaking URLs.
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}
RESERVED = ("login", "logout", "static", "api")
# Literal class names so Tailwind's scanner finds them.
SPANS = {3: "xl:col-span-3", 4: "xl:col-span-4", 6: "xl:col-span-6", 8: "xl:col-span-8", 12: "xl:col-span-12"}

Authenticate = Callable[[str, str], Awaitable[str | None]]
Permission = Callable[[str, str, ModelAdmin], bool]


def _with_msg(url: str, msg: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}{urlencode({'msg': msg})}"


@dataclass
class Page:
    """A custom screen from `@admin.page(...)`. Quacks enough like a ModelAdmin for navigation and permissions."""
    name: str
    slug: str
    icon: str
    group: str
    key: str | None
    fn: Callable

    @property
    def name_plural(self) -> str:
        return self.name

    def has_permission(self, user: str, perm: str) -> bool:
        return True


@dataclass
class Widget:
    title: str
    icon: str
    span: int
    fn: Callable


class Admin:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        secret_key: str,
        authenticate: Authenticate,
        title: str = "Admin",
        accent: str = "#ff6a2b",
        logo_icon: str = "layers",
        permission: Permission | None = None,
        groups: dict[str, str] | None = None,
        site_url: str | None = None,
        login_attempts: int = 10,
        media_dir: str | Path | None = None,
        storage=None,
        max_upload: int = 10 * 1024 * 1024,
        secure_cookies: bool | None = None,
        templates_dir: str | Path | list[str | Path] | None = None,
        api: bool = False,
        api_auth: Callable | None = None,
        cookie_path: str | None = None,
        owners: list[str] | None = None,
    ):
        self.engine = engine
        self.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        self.signer = Signer(secret_key)
        self.authenticate = authenticate
        self.title = title
        self.accent = accent
        self.logo_icon = logo_icon
        self.permission = permission
        self.group_icons = groups or {}
        self.site_url = site_url
        self.login_attempts = login_attempts
        self.storage = storage or (LocalStorage(media_dir) if media_dir else None)
        self.max_upload = max_upload
        self.api = api or api_auth is not None   # JSON API at {path}/api/{model}, see api.py
        self.api_auth = api_auth                 # a FastAPI dependency returning the user (bearer token, API key…)
        self.secure_cookies = secure_cookies
        self._cookie_path = cookie_path
        self.owners = frozenset(owners or ())    # set → roles are on: owners can do anything, everyone else what their roles grant          # default: the mount path. "/" lets admin.current_user work on your other routes.     # None: follow the request scheme. Set True behind a TLS-terminating proxy.
        self.models: dict[str, ModelAdmin] = {}
        self.pages: dict[str, Page] = {}
        self.widgets: list[Widget] = []
        self.prefix = ""
        self._by_model: dict[type, ModelAdmin] = {}
        self._key_fields: dict[type, Field] = {}
        # ponytail: failed sign-ins live in this process; with several workers each counts its own. Use Redis to share them.
        self._failures: dict[str, list[float]] = {}
        self._log_ready = False
        # Your templates_dir wins; "original/fastyfold/..." always reaches the built-in one, so an override can extend it.
        package = FileSystemLoader(HERE / "templates")
        mine = [FileSystemLoader(d) for d in ([templates_dir] if isinstance(templates_dir, (str, Path)) else templates_dir or [])]
        env = Environment(loader=ChoiceLoader([*mine, package, PrefixLoader({"original": package})]), autoescape=True)
        self.templates = Jinja2Templates(env=env)
        self.templates.env.globals["admin"] = self
        self.templates.env.globals["tone"] = lambda text: sum(map(ord, str(text or ""))) % 5
        self.router = APIRouter(route_class=self._route_class())
        self._routes()

    # ── setup ─────────────────────────────────────────────────────────────
    def register(self, cls: type[ModelAdmin]) -> type[ModelAdmin]:
        """Register a ModelAdmin subclass. Usable as a decorator."""
        ma = cls(self)
        self._claim(ma.slug)
        if self.storage is None and any(f.kind in ("image", "file") for f in ma.fields.values()):
            raise ValueError(f"fastyfold: {cls.__name__} has file fields; pass Admin(media_dir=...) or Admin(storage=...)")
        self.models[ma.slug] = ma
        self._by_model[ma.model] = ma
        return cls

    def page(self, name: str, *, icon: str = "dashboard", group: str = "Pages", key: str | None = None, slug: str | None = None):
        """Add a custom screen: `async def fn(request, user)` returning HTML (str) or any Response.

        The HTML is inserted as-is inside the admin layout, so escape untrusted values (`markupsafe.escape`).
        POSTs to the page need the CSRF token: put `<input type="hidden" name="_csrf" value="...">` from `admin.csrf(request)`.
        """
        def wrap(fn):
            p = Page(name, slug or re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"), icon, group, key, fn)
            self._claim(p.slug)
            self.pages[p.slug] = p
            return fn
        return wrap

    def widget(self, title: str, *, icon: str = "insights", span: int = 3):
        """Add a dashboard card: `async def fn(session, user)` returning a number or text, `{"value", "caption"}`,
        a list of `(label, number)` pairs (drawn as bars), or None to hide the card."""
        if span not in SPANS:
            raise ValueError(f"fastyfold: widget span must be one of {sorted(SPANS)}")

        def wrap(fn):
            self.widgets.append(Widget(title, icon, span, fn))
            return fn
        return wrap

    def _claim(self, slug: str) -> None:
        if slug in self.models or slug in self.pages or slug.startswith("_") or slug in RESERVED or not slug:
            raise ValueError(f"fastyfold: slug {slug!r} is taken or reserved")

    def mount(self, app: FastAPI, path: str = "/admin") -> None:
        self.prefix = path.rstrip("/")
        # Static first, so /admin/static/... never reaches the /{model}/{pk} route.
        app.mount(f"{self.prefix}/static", StaticFiles(directory=HERE / "static"), name="fastyfold-static")
        # Both before the admin router, whose /{model} and /{model}/{pk} would match them.
        if self.owners:
            app.include_router(roles.build(self), prefix=self.prefix)
        if self.api:
            from . import api
            app.include_router(api.build(self), prefix=self.prefix)
        app.include_router(self.router, prefix=self.prefix)

    @asynccontextmanager
    async def session(self):
        if not self._log_ready:
            await audit.ensure_table(self.engine)
            if self.owners:
                await roles.ensure_tables(self.engine)
            self._log_ready = True
        async with self.sessionmaker() as s:
            yield s

    def url(self, *parts, **query) -> str:
        parts = [str(p) for p in parts if p != ""]
        path = "/".join([self.prefix, *parts]) if parts else self.prefix + "/"
        query = {k: v for k, v in query.items() if v not in (None, "")}
        return path + ("?" + urlencode(query, doseq=True) if query else "")

    def file_url(self, key: str | None) -> str:
        if not key:
            return ""
        url = getattr(self.storage, "url", None)
        return url(key) if url else self.url("_media", key)

    async def current_user(self, request: Request) -> str:
        """FastAPI dependency for your own routes: `user: str = Depends(admin.current_user)`.
        Gives the signed-in admin user, or redirects to the admin's login."""
        return await self._user(request)

    async def end_sessions(self, user: str) -> int:
        """Sign `user` out everywhere (after a password change, or when someone leaves). Returns how many sessions ended."""
        async with self.session() as s:
            result = await s.execute(sa_delete(AdminSession).where(AdminSession.user == user))
            await s.commit()
        return result.rowcount

    @property
    def cookie_path(self) -> str:
        return self._cookie_path or self.prefix or "/"

    def _secure_cookie(self, request: Request) -> bool:
        return self.secure_cookies if self.secure_cookies is not None else request.url.scheme == "https"

    def csrf(self, request: Request) -> str:
        """The CSRF token for this request's session, for forms on custom pages."""
        return self.signer.csrf(request.cookies.get(SESSION_COOKIE))

    def label(self, obj) -> str:
        """How a record is named anywhere in the admin: its ModelAdmin's label, else str(obj)."""
        ma = self._by_model.get(type(obj))
        return ma.label(obj) if ma else str(obj)

    def _local(self, url: str, default: str) -> str:
        """`url` if it stays inside the admin, else `default`. Blocks `//host` and `/\\host`."""
        inside = url.startswith(self.prefix + "/") and not url.startswith(("//", "/\\"))
        return url if inside else default

    def can(self, user: str, perm: str, ma: ModelAdmin | Page, obj=None) -> bool:
        """The global hook, then the model's has_permission, then (for one record) has_object_permission."""
        if self.permission and not self.permission(user, perm, ma):
            return False
        if self.owners and user not in self.owners and (ma.slug, perm) not in (roles.current_grants.get() or ()):
            return False
        if not ma.has_permission(user, perm):
            return False
        return obj is None or ma.has_object_permission(user, perm, obj)

    def _require(self, user: str, perm: str, ma: ModelAdmin, obj) -> None:
        if not self.can(user, perm, ma, obj):
            raise HTTPException(403, "You don't have permission to do that.")

    def groups(self, user: str) -> list[dict]:
        """Models and pages the user may view, grouped for the rail and context panel."""
        out: dict[str, dict] = {}
        for item in [*self.models.values(), *self.pages.values()]:
            if not self.can(user, "view", item):
                continue
            g = out.setdefault(item.group, {"name": item.group, "icon": self.group_icons.get(item.group, item.icon),
                                            "models": [], "pages": [], "items": []})
            g["pages" if isinstance(item, Page) else "models"].append(item)
            g["items"].append(item)
        return list(out.values())

    def _visible(self, user: str) -> list[ModelAdmin]:
        return [ma for g in self.groups(user) for ma in g["models"]]

    def _log_models(self, user: str) -> list[str]:
        """Audit entries this user may read: their models', plus role changes for owners."""
        return [ma.slug for ma in self._visible(user)] + ([roles.AUDIT_MODEL] if user in self.owners else [])

    # ── request helpers ───────────────────────────────────────────────────
    def _route_class(self) -> type[APIRoute]:
        """Every admin route renders its errors as admin pages and carries SECURITY_HEADERS.
        A route class (not an app exception handler) keeps this inside the admin and out of the host app."""
        admin = self

        class AdminRoute(APIRoute):
            def get_route_handler(self):
                handler = super().get_route_handler()

                async def route(request: Request) -> Response:
                    try:
                        response = await handler(request)
                    except HTTPException as exc:
                        response = admin._error(request, exc)
                    except RequestValidationError:
                        if not admin._wants_html(request):
                            raise                       # FastAPI's own 422 body for API clients
                        response = admin._error(request, HTTPException(422, "Some values in this address aren't valid."))
                    for name, value in SECURITY_HEADERS.items():
                        response.headers.setdefault(name, value)
                    return response
                return route
        return AdminRoute

    def _error(self, request: Request, exc: HTTPException) -> Response:
        headers = dict(exc.headers or {})
        if 300 <= exc.status_code < 400 and "Location" in headers:
            return RedirectResponse(headers.pop("Location"), exc.status_code, headers=headers)
        if self._wants_html(request):
            return self._render(request, "error.html", exc.status_code, user=getattr(request.state, "admin_user", None),
                               code=exc.status_code, detail=exc.detail)
        return JSONResponse({"detail": exc.detail}, exc.status_code, headers=headers)

    @staticmethod
    def _wants_html(request: Request) -> bool:
        return "text/html" in request.headers.get("accept", "") and not request.headers.get("HX-Request")

    async def _user(self, request: Request) -> str:
        """The signed-in user, from a signed cookie that names a live row in fastyfold_session."""
        data = self.signer.load(request.cookies.get(SESSION_COOKIE))
        user = None
        if data:
            async with self.session() as s:
                user = await s.scalar(select(AdminSession.user).where(
                    AdminSession.id == data[1], AdminSession.user == data[0], AdminSession.expires_at > datetime.now(timezone.utc)))
        if user:
            await self._signed_in(request, user)
            return user
        login = self.url("login", next=str(request.url.path))
        if request.headers.get("HX-Request"):
            raise HTTPException(401, headers={"HX-Redirect": login})
        raise HTTPException(303, headers={"Location": login})

    async def _signed_in(self, request: Request, user: str) -> None:
        """Remember who is asking, and (with roles on) what their roles grant, for the rest of this request."""
        request.state.admin_user = user
        if self.owners and user not in self.owners:
            async with self.session() as s:
                roles.current_grants.set(await roles.grants_for(s, user))

    async def _csrf(self, request: Request) -> None:
        token = request.headers.get("X-CSRF-Token") or (await request.form()).get("_csrf")
        if not self.signer.csrf_ok(request.cookies.get(SESSION_COOKIE), token):
            raise HTTPException(403, "Invalid or missing CSRF token. Reload the page and try again.")

    def _model(self, slug: str, user: str, perm: str = "view") -> ModelAdmin:
        ma = self.models.get(slug)
        if not ma:
            raise HTTPException(404, "Unknown model")
        if not self.can(user, perm, ma):
            raise HTTPException(403, "You don't have permission to do that.")
        return ma

    def render(self, request: Request, template: str, status: int = 200, **ctx) -> HTMLResponse:
        """Render any template (yours from templates_dir, or "fastyfold/...") with the admin's layout context:
        user, csrf, nav, jumps and msg. Custom pages return this to use their own templates."""
        cookie = request.cookies.get(SESSION_COOKIE)
        ctx.setdefault("user", getattr(request.state, "admin_user", None))
        ctx.setdefault("current", None)
        ctx["csrf"] = self.signer.csrf(cookie) if cookie else ""
        ctx["nav"] = self.groups(ctx["user"]) if ctx["user"] else []
        ctx["jumps"] = {"d": self.url(""), **{m.key.lower(): self.url(m.slug) for g in ctx["nav"] for m in g["items"] if m.key}}
        ctx["msg"] = request.query_params.get("msg", "")
        return self.templates.TemplateResponse(request, template, ctx, status_code=status)

    def _render(self, request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
        return self.render(request, f"fastyfold/{name}", status, **ctx)

    @staticmethod
    def _greeting() -> str:
        h = datetime.now().hour
        return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"

    def _blocked(self, ip: str) -> bool:
        now = time.monotonic()
        hits = [t for t in self._failures.get(ip, []) if now - t < LOGIN_WINDOW]
        if hits:
            self._failures[ip] = hits
        else:
            self._failures.pop(ip, None)
        return bool(self.login_attempts) and len(hits) >= self.login_attempts

    @staticmethod
    def _list_args(ma: ModelAdmin, p) -> tuple[str, dict[str, str], str | None]:
        return p.get("q", ""), {f["name"]: p.get(f"f_{f['name']}", "") for f in ma.filter_options()}, p.get("sort") or None

    @staticmethod
    def _csv(ma: ModelAdmin, rows) -> Response:
        # ponytail: builds the whole file in memory; stream with session.stream_scalars() for very large exports.
        buf = io.StringIO()
        out = csv.writer(buf)
        out.writerow([ma.column_label(n) for n in ma.export_fields])
        for obj in rows:
            out.writerow([ma.export_value(obj, n) for n in ma.export_fields])
        return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{ma.slug}.csv"'})

    @staticmethod
    def _files(ma: ModelAdmin, obj) -> list[str]:
        return [getattr(obj, n) for n, f in ma.fields.items() if f.kind in ("image", "file") and getattr(obj, n)]

    # ── routes ────────────────────────────────────────────────────────────
    def _routes(self) -> None:
        r = self.router
        User = Depends(self._user)
        Csrf = Depends(self._csrf)

        @r.get("/login", response_class=HTMLResponse, include_in_schema=False)
        async def login_page(request: Request, next: str = ""):
            return self._render(request, "login.html", next=next, user=None)

        @r.post("/login", include_in_schema=False)
        async def login(request: Request):
            form = await request.form()
            username, password = str(form.get("username", "")), str(form.get("password", ""))
            next_url = str(form.get("next", ""))
            # Behind a proxy, run uvicorn with --proxy-headers so this is the visitor's address.
            ip = request.client.host if request.client else "unknown"
            if self._blocked(ip):
                return self._render(request, "login.html", 429, next=next_url, user=None, username=username,
                                   error="Too many failed sign-ins. Try again in 15 minutes.")
            user = await self.authenticate(username, password)
            if not user:
                self._failures.setdefault(ip, []).append(time.monotonic())
                return self._render(request, "login.html", 401, next=next_url, user=None, error="That username and password didn't match.", username=username)
            self._failures.pop(ip, None)
            remember = bool(form.get("remember"))
            sid, now = new_session_id(), datetime.now(timezone.utc)
            async with self.session() as s:
                await s.execute(sa_delete(AdminSession).where(AdminSession.expires_at <= now))   # sweep stale rows
                s.add(AdminSession(id=sid, user=user, expires_at=now + timedelta(seconds=SESSION_MAX_AGE if remember else SESSION_SHORT_AGE)))
                await s.commit()
            resp = RedirectResponse(self._local(next_url, self.url("")), 303)
            resp.set_cookie(SESSION_COOKIE, self.signer.dump(user, sid), max_age=SESSION_MAX_AGE if remember else None,
                            httponly=True, samesite="lax", secure=self._secure_cookie(request), path=self.cookie_path)
            return resp

        @r.post("/logout", include_in_schema=False, dependencies=[Csrf])
        async def logout(request: Request, user: str = User):
            if data := self.signer.load(request.cookies.get(SESSION_COOKIE)):
                async with self.session() as s:
                    await s.execute(sa_delete(AdminSession).where(AdminSession.id == data[1]))
                    await s.commit()
            resp = RedirectResponse(self.url("login"), 303)
            resp.delete_cookie(SESSION_COOKIE, path=self.cookie_path)
            return resp

        @r.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def dashboard(request: Request, user: str = User):
            visible = self._visible(user)
            async with self.session() as s:
                counts = {ma.slug: await ma.count(s, ma.get_query(request)) for ma in visible}
                lead = next((ma for ma in visible if ma.board_field), visible[0] if visible else None)
                mix = await lead.board_counts(s, lead.get_query(request)) if lead and lead.board_field else {}
                latest = (await s.scalars(lead.get_query(request).order_by(getattr(lead.model, lead.pk).desc()).limit(5))).all() if lead else []
                activity = await audit.recent(s, models=self._log_models(user), limit=6)
                widgets = [(w, value) for w in self.widgets if (value := await w.fn(s, user)) is not None]
            return self._render(request, "dashboard.html", user=user, models=visible, counts=counts, lead=lead,
                               mix=mix, latest=latest, activity=activity, widgets=widgets, spans=SPANS, greeting=self._greeting())

        @r.get("/_search", include_in_schema=False)
        async def search(request: Request, q: str = "", user: str = User):
            q = q.strip()
            groups = []
            nav = [item for g in self.groups(user) for item in g["items"]]
            visible = [m for m in nav if isinstance(m, ModelAdmin)]
            if q:
                records = []
                async with self.session() as s:
                    for ma in visible:
                        if not ma.search_fields:
                            continue
                        for obj in (await s.scalars(ma.list_query(q=q, request=request).limit(3))).all():
                            sub = ma.subtitle(obj) or f"{ma.name} #{ma.pk_of(obj)}"
                            records.append({"label": ma.label(obj), "sub": sub, "url": self.url(ma.slug, ma.pk_of(obj)), "icon": ma.icon})
                groups.append({"title": "Records", "items": records[:8]})
            ql = q.lower()
            groups.append({"title": "Models", "items": [
                {"label": m.name_plural, "sub": f"Go to {m.name_plural.lower()}", "url": self.url(m.slug), "icon": m.icon,
                 "keys": ["G", m.key.upper()] if m.key else None}
                for m in nav if not q or ql in m.name_plural.lower()]})
            actions = [{"label": f"New {ma.name.lower()}", "sub": ma.name_plural, "url": self.url(ma.slug, "new"), "icon": "add"}
                       for ma in visible if self.can(user, "add", ma) and (not q or ql in f"new {ma.name.lower()}")]
            if q:
                actions += [{"label": f"Export {ma.name_plural.lower()}", "sub": "CSV", "url": self.url(ma.slug, "export.csv"), "icon": "download"}
                            for ma in visible if self.can(user, "export", ma) and ql in f"export {ma.name_plural.lower()}"]
            commands = [("Toggle dark mode", "dark_mode", "theme", ["T"], None), ("Keyboard shortcuts", "keyboard", "keys", ["?"], None),
                        ("Audit log", "history", None, None, self.url("_log"))]
            if user in self.owners:
                commands.append(("Roles and permissions", "shield_person", None, None, self.url("_roles")))
            for label, icon, cmd, keys, url in commands:
                if not q or ql in label.lower():
                    actions.append({"label": label, "icon": icon, "cmd": cmd, "keys": keys, "url": url, "sub": "fastyfold"})
            groups.append({"title": "Actions", "items": actions})
            return JSONResponse({"groups": [g for g in groups if g["items"]]})

        @r.get("/_log", response_class=HTMLResponse, include_in_schema=False)
        async def log(request: Request, page: int = 1, who: str = "", model: str = "", action: str = "", days: str = "",
                      user: str = User):
            models = self._visible(user)
            visible = self._log_models(user)
            since = None
            if days in DAYS:
                since = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=DAYS[days])
            filters = {"who": who, "model": model if model in visible else "", "action": action, "days": days if since else ""}
            async with self.session() as s:
                entries = await audit.recent(s, models=[model] if filters["model"] else visible, user=who or None,
                                             action=action or None, since=since, limit=51, offset=(max(page, 1) - 1) * 50)
                options = {"who": [(u, u) for u in await audit.distinct(s, "user", models=visible)],
                           "model": [(ma.slug, ma.name_plural) for ma in models],
                           "action": [(a, a) for a in await audit.distinct(s, "action", models=visible)],
                           "days": [("today", "Today"), ("7d", "Last 7 days"), ("30d", "Last 30 days")]}
            return self._render(request, "log.html", user=user, entries=entries[:50], page=page, has_more=len(entries) > 50,
                               filters=filters, options=options)

        @r.get("/_media/{key:path}", include_in_schema=False)
        async def media(key: str, user: str = User):
            path = self.storage.path(key) if isinstance(self.storage, LocalStorage) else None
            if path is None:
                raise HTTPException(404, "Not found")
            ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            # Only real image types render inline; everything else downloads, sandboxed and never sniffed.
            return FileResponse(path, media_type=ctype if ctype in IMAGE_TYPES else "application/octet-stream", filename=path.name,
                                content_disposition_type="inline" if ctype in IMAGE_TYPES else "attachment",
                                headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"})

        @r.get("/{slug}", response_class=HTMLResponse, include_in_schema=False)
        async def list_view(request: Request, slug: str, user: str = User):
            if slug in self.pages:
                return await self._page(request, self.pages[slug], user)
            ma = self._model(slug, user)
            p = request.query_params
            view = p.get("view") or ("board" if ma.board_field else "table")
            if view == "board" and not ma.board_field:
                view = "table"
            q, filters, sort = self._list_args(ma, p)
            raw_page, raw_per = p.get("page", ""), p.get("per_page", "")
            page = max(1, int(raw_page)) if raw_page.isdigit() else 1
            per_page = int(raw_per) if raw_per.isdigit() and int(raw_per) in ma.per_page_options else ma.per_page
            stmt = ma.list_query(q=q, filters=filters, sort=sort, request=request)
            async with self.session() as s:
                total = await ma.count(s, stmt)
                per = per_page * (4 if view == "board" else 1)
                # ponytail: OFFSET paging slows down past ~100k rows; switch to keyset paging on the sort column.
                rows = (await s.scalars(stmt.limit(per).offset((page - 1) * per))).all()
                counts = await ma.board_counts(s, ma.get_query(request))
                labels = {n: await self._labels(s, ma.fields[n].target, [v]) for n, v in filters.items() if v and ma.fields[n].kind == "fk"}
            chips = [{"name": n, "label": ma.fields[n].label, "text": ma.filter_text(n, v, labels.get(n, {}))}
                     for n, v in filters.items() if ma.filter_condition(n, v) is not None]
            state = {"view": view, "q": q, "sort": sort, "page": page, "per_page": per_page if per_page != ma.per_page else None,
                     **{f"f_{k}": v for k, v in filters.items()}}
            return self._render(request, "list.html", user=user, ma=ma, current=ma, rows=rows, total=total, page=page,
                               pages=max(1, -(-total // per)), view=view, q=q, filters=filters, sort=sort or "",
                               counts=counts, state=state, chips=chips, fk_labels=labels, per_page=per_page)

        @r.post("/{slug}", include_in_schema=False, dependencies=[Csrf])
        async def page_post(request: Request, slug: str, user: str = User):
            if slug not in self.pages:
                raise HTTPException(405, "Method not allowed")
            return await self._page(request, self.pages[slug], user)

        @r.get("/{slug}/export.csv", include_in_schema=False)
        async def export(request: Request, slug: str, user: str = User):
            ma = self._model(slug, user, "export")
            q, filters, sort = self._list_args(ma, request.query_params)
            async with self.session() as s:
                rows = (await s.scalars(ma.list_query(q=q, filters=filters, sort=sort, request=request))).all()
            return self._csv(ma, rows)

        @r.get("/{slug}/_lookup/{path}", include_in_schema=False)
        async def lookup(request: Request, slug: str, path: str, q: str = "", user: str = User):
            ma = self._model(slug, user)
            f = ma.lookup_field(path)
            if f is None:
                raise HTTPException(404, "Unknown field")
            target = self._by_model.get(f.target)
            if target is not None and not self.can(user, "view", target):
                raise HTTPException(403, "You don't have permission to do that.")
            key = self._key_field(f.target).name
            async with self.session() as s:
                if target is not None:
                    stmt = target.list_query(q=q.strip(), request=request)
                else:
                    stmt = select(f.target).order_by(getattr(f.target, key))
                    text = [getattr(f.target, n) for n, x in fields_for(sa_inspect(f.target)).items() if x.kind == "str"]
                    if q.strip() and text:
                        stmt = stmt.where(or_(*[c.icontains(q.strip(), autoescape=True) for c in text]))
                rows = (await s.scalars(stmt.limit(20))).all()
            return JSONResponse({"results": [{"value": str(getattr(o, key)), "label": self.label(o)} for o in rows]})

        @r.get("/{slug}/new", response_class=HTMLResponse, include_in_schema=False)
        async def new_form(request: Request, slug: str, user: str = User):
            ma = self._model(slug, user, "add")
            async with self.session() as s:
                return await self._form(request, s, ma, user, None)

        @r.post("/{slug}/new", include_in_schema=False, dependencies=[Csrf])
        async def create(request: Request, slug: str, user: str = User):
            ma = self._model(slug, user, "add")
            return await self._save(request, ma, user, None)

        @r.post("/{slug}/action", include_in_schema=False, dependencies=[Csrf])
        async def run_action(request: Request, slug: str, user: str = User):
            form = await request.form()
            name = str(form.get("action", ""))
            ma = self._model(slug, user, name)
            if name not in ("delete", "export") and name not in ma.actions:
                raise HTTPException(400, "Unknown action")
            back = self._local(str(form.get("next", "")), self.url(ma.slug))
            ids = [i for i in form.getlist("ids") if i]
            select_all = bool(form.get("select_all"))
            if not ids and not select_all:
                return RedirectResponse(self.url(ma.slug, msg="Select at least one row first"), 303)
            if select_all:
                # Every row matching the list's search and filters, not just the page on screen.
                q, filters, sort = self._list_args(ma, QueryParams(str(form.get("query", ""))))
                stmt = ma.list_query(q=q, filters=filters, sort=sort, request=request).limit(ma.select_all_limit + 1)
            else:
                try:
                    keys = [ma.fields[ma.pk].parse(i) for i in ids]
                except FieldError:
                    raise HTTPException(400, "Invalid id") from None
                stmt = ma.get_query(request).where(getattr(ma.model, ma.pk).in_(keys)).order_by(getattr(ma.model, ma.pk))
            stale = []
            async with self.session() as s:
                objs = (await s.scalars(stmt)).all()
                if len(objs) > ma.select_all_limit:
                    msg = f"That matches more than {ma.select_all_limit:,} {ma.name_plural.lower()}. Narrow the search or filters first."
                    return RedirectResponse(_with_msg(back, msg), 303)
                for o in objs:
                    self._require(user, name, ma, o)
                if name == "export":
                    return self._csv(ma, objs)
                if name == "delete":
                    for o in objs:
                        audit.record(s, user=user, action="delete", model=ma.slug, obj_id=ma.pk_of(o), obj_repr=ma.label(o))
                        stale += self._files(ma, o)
                        await call_hook(ma.delete, s, o)
                    label = "Deleted"
                else:
                    await call_hook(getattr(ma, name), s, objs)
                    for o in objs:
                        audit.record(s, user=user, action=name, model=ma.slug, obj_id=ma.pk_of(o), obj_repr=ma.label(o))
                    label = ma.actions[name]["label"]
                try:
                    await s.commit()
                    msg = f"{label}: {len(objs)} {ma.name_plural.lower()}"
                except IntegrityError as e:
                    await s.rollback()
                    stale, objs, msg = [], [], f"Nothing changed: {db_message(e)}"
            for key in stale:
                await self.storage.delete(key)
            if name == "delete":
                for o in objs:
                    await call_hook(ma.after_delete, o)
            return RedirectResponse(_with_msg(back, msg), 303)

        @r.get("/{slug}/{pk}/preview", response_class=HTMLResponse, include_in_schema=False)
        async def preview(request: Request, slug: str, pk: str, user: str = User):
            ma = self._model(slug, user)
            async with self.session() as s:
                obj = await self._get(s, ma, pk, request)
                history = await audit.recent(s, model=ma.slug, object_id=pk, limit=5)
            return self._render(request, "partials/preview.html", user=user, ma=ma, obj=obj, history=history)

        @r.get("/{slug}/{pk}", response_class=HTMLResponse, include_in_schema=False)
        async def edit_form(request: Request, slug: str, pk: str, user: str = User):
            ma = self._model(slug, user)
            async with self.session() as s:
                obj = await self._get(s, ma, pk, request)
                return await self._form(request, s, ma, user, obj)

        @r.post("/{slug}/{pk}", include_in_schema=False, dependencies=[Csrf])
        async def update(request: Request, slug: str, pk: str, user: str = User):
            ma = self._model(slug, user, "change")
            return await self._save(request, ma, user, pk)

        @r.post("/{slug}/{pk}/delete", include_in_schema=False, dependencies=[Csrf])
        async def delete(request: Request, slug: str, pk: str, user: str = User):
            ma = self._model(slug, user, "delete")
            async with self.session() as s:
                obj = await self._get(s, ma, pk, request, "delete")
                label, stale = ma.label(obj), self._files(ma, obj)
                await call_hook(ma.delete, s, obj)
                audit.record(s, user=user, action="delete", model=ma.slug, obj_id=pk, obj_repr=label)
                try:
                    await s.commit()
                except IntegrityError as e:
                    await s.rollback()
                    return RedirectResponse(self.url(ma.slug, pk, msg=f"Couldn't delete {label}: {db_message(e)}"), 303)
            for key in stale:
                await self.storage.delete(key)
            await call_hook(ma.after_delete, obj)
            return RedirectResponse(self.url(ma.slug, msg=f"Deleted {label}"), 303)

        @r.post("/{slug}/{pk}/move", include_in_schema=False, dependencies=[Csrf])
        async def move(request: Request, slug: str, pk: str, user: str = User):
            ma = self._model(slug, user, "change")
            if not ma.board_field:
                raise HTTPException(400, "This model has no board")
            f = ma.fields[ma.board_field]
            try:
                value = f.parse((await request.form()).get("value"))
            except FieldError as e:
                raise HTTPException(400, str(e)) from None
            async with self.session() as s:
                obj = await self._get(s, ma, pk, request, "change")
                old = ma.raw(obj, ma.board_field)
                setattr(obj, ma.board_field, value)
                audit.record(s, user=user, action="change", model=ma.slug, obj_id=pk, obj_repr=ma.label(obj),
                             changes={ma.board_field: [old, ma.raw(obj, ma.board_field)]})
                await s.commit()
            return Response(status_code=204)

    # ── pages ─────────────────────────────────────────────────────────────
    async def _page(self, request: Request, page: Page, user: str):
        if not self.can(user, "view", page):
            raise HTTPException(403, "You don't have permission to do that.")
        out = await page.fn(request, user)
        if isinstance(out, Response):
            return out
        return self._render(request, "page.html", user=user, page=page, current=page, body=Markup(out or ""))  # noqa: S704 - page HTML is the app author's own, documented as such

    # ── related rows ──────────────────────────────────────────────────────
    def _key_field(self, target: type) -> Field:
        if target not in self._key_fields:
            mapper = sa_inspect(target)
            self._key_fields[target] = fields_for(mapper)[mapper.primary_key[0].key]
        return self._key_fields[target]

    async def _objects(self, s, target: type, ids: list[str], request: Request) -> list | None:
        """The rows for `ids` that this request may pick (the target's get_query), or None if any is malformed or out of reach."""
        kf = self._key_field(target)
        try:
            keys = {kf.parse(i) for i in ids}
        except FieldError:
            return None
        if not keys:
            return []
        ma = self._by_model.get(target)
        scope = ma.get_query(request) if ma is not None else select(target)
        rows = (await s.scalars(scope.where(getattr(target, kf.name).in_(keys)))).all()
        return list(rows) if len(rows) == len(keys) else None

    async def _labels(self, s, target: type, ids) -> dict[str, str]:
        kf = self._key_field(target)
        keys = set()
        for i in ids:
            try:
                if i not in ("", None):
                    keys.add(kf.parse(str(i)))
            except FieldError:
                pass
        if not keys:
            return {}
        rows = (await s.scalars(select(target).where(getattr(target, kf.name).in_(keys)))).all()
        return {str(getattr(o, kf.name)): self.label(o) for o in rows}

    # ── form helpers ──────────────────────────────────────────────────────
    async def _get(self, s, ma: ModelAdmin, pk: str, request: Request, perm: str = "view"):
        """One record inside get_query(request), or 404; then 403 unless the user may `perm` it."""
        try:
            key = ma.fields[ma.pk].parse(pk)
        except FieldError:
            raise HTTPException(404, "Not found") from None
        obj = await s.scalar(ma.get_query(request).where(getattr(ma.model, ma.pk) == key))
        if obj is None:
            raise HTTPException(404, f"{ma.name} not found")
        self._require(request.state.admin_user, perm, ma, obj)
        return obj

    async def _form(self, request, s, ma: ModelAdmin, user: str, obj, values: dict | None = None, errors: dict | None = None,
                    status: int = 200, inline_rows: dict | None = None):
        errors = errors or {}
        if values is None:
            values = {}
            for n in ma.form_fields:
                f = ma.fields[n]
                if f.kind == "m2m":
                    key = self._key_field(f.target).name
                    values[n] = [str(getattr(o, key)) for o in getattr(obj, n)] if obj is not None else []
                else:
                    values[n] = f.form_value(getattr(obj, n) if obj is not None else f.default)
        if inline_rows is None:
            inline_rows = {}
            for inl in ma.inlines:
                children = sorted(getattr(obj, inl.name), key=lambda c: getattr(c, inl.pk)) if obj is not None else []
                inline_rows[inl.name] = [{"i": i, "id": str(getattr(c, inl.pk)), "deleted": False,
                                          "values": {n: inl.fields[n].form_value(getattr(c, n)) for n in inl.names}}
                                         for i, c in enumerate(children)]
        # Labels for every autocomplete's current value(s).
        selected = {}
        for n in ma.form_fields:
            f = ma.fields[n]
            if f.kind in ("fk", "m2m") and f.target:
                ids = values.get(n) if f.kind == "m2m" else [values.get(n)]
                labels = await self._labels(s, f.target, ids)
                selected[n] = [{"value": i, "label": labels[i]} for i in ids if i in labels]
        for inl in ma.inlines:
            for n in inl.names:
                f = inl.fields[n]
                if f.kind == "fk" and f.target:
                    labels = await self._labels(s, f.target, [row["values"][n] for row in inline_rows[inl.name]])
                    for row in inline_rows[inl.name]:
                        v = row["values"][n]
                        selected[f"{inl.name}-{row['i']}-{n}"] = [{"value": v, "label": labels[v]}] if v in labels else []
        history = await audit.recent(s, model=ma.slug, object_id=ma.pk_of(obj), limit=6) if obj is not None else []
        actions = [a for a in ma.actions.values() if self.can(user, a["name"], ma, obj)] if obj is not None else []
        multipart = any(ma.fields[n].kind in ("image", "file") for n in ma.form_fields)
        return self._render(request, "form.html", status, user=user, ma=ma, current=ma, obj=obj, values=values, errors=errors,
                           selected=selected, inline_rows=inline_rows, history=history, actions=actions, multipart=multipart,
                           can_change=self.can(user, "change", ma, obj), can_delete=obj is not None and self.can(user, "delete", ma, obj))

    def _assign(self, ma: ModelAdmin, obj, data: dict, targets: dict, is_new: bool) -> dict:
        """Set parsed values and m2m rows on `obj`; return the audit changes {field: [old, new]}."""
        changes = {}
        for k, v in data.items():
            old = getattr(obj, k, None)
            if old != v:
                changes[k] = [ma.fields[k].display(old) if not is_new else None, ma.fields[k].display(v)]
                setattr(obj, k, v)
        for name, objs in targets.items():
            old = list(getattr(obj, name))
            if {id(o) for o in old} != {id(o) for o in objs}:   # same session, so one instance per row
                changes[name] = [", ".join(map(self.label, old)) or None, ", ".join(map(self.label, objs)) or None]
                setattr(obj, name, objs)
        return changes

    @staticmethod
    def _inline_rows(form, inl: Inline, errors: dict) -> list[dict]:
        """Rows posted as `<inline>-<i>-<field>`, parsed; blank new rows are dropped."""
        pattern = re.compile(rf"{re.escape(inl.name)}-(\d+)-")
        rows = []
        for i in sorted({int(m.group(1)) for k in form.keys() if (m := pattern.match(k))}):
            prefix = f"{inl.name}-{i}-"
            raw = {n: form.get(prefix + n) for n in inl.names}
            row = {"i": i, "id": str(form.get(prefix + "id") or ""), "deleted": bool(form.get(prefix + "_delete")),
                   "values": {n: str(v or "") for n, v in raw.items()}, "data": {}}
            if not row["id"] and not any(v for n, v in row["values"].items() if inl.fields[n].kind != "bool"):
                continue
            if not row["deleted"]:
                for n in inl.names:
                    try:
                        row["data"][n] = inl.fields[n].parse(raw[n])
                    except FieldError as e:
                        errors[prefix + n] = str(e)
            rows.append(row)
        return rows

    @staticmethod
    async def _apply_inline(s, obj, inl: Inline, rows: list[dict]) -> str:
        collection = getattr(obj, inl.name)
        existing = {str(getattr(c, inl.pk)): c for c in collection}
        added = changed = removed = 0
        for row in rows:
            child = existing.get(row["id"])
            if row["id"] and child is None:
                continue        # not one of this record's children
            if row["deleted"]:
                if child is not None:
                    collection.remove(child)
                    await s.delete(child)
                    removed += 1
                continue
            if child is None:
                child = inl.model()
                collection.append(child)
                added += 1
            elif any(getattr(child, k) != v for k, v in row["data"].items()):
                changed += 1
            for k, v in row["data"].items():
                setattr(child, k, v)
        return ", ".join(f"{n} {word}" for n, word in ((added, "added"), (changed, "changed"), (removed, "removed")) if n)

    async def _save(self, request: Request, ma: ModelAdmin, user: str, pk: str | None):
        form = await request.form()
        stale, fresh = [], []          # files to delete after commit / files this request stored

        def posted(obj) -> dict:
            """What the user typed, for re-rendering the form."""
            values = {}
            for n in ma.form_fields:
                kind = ma.fields[n].kind
                if kind == "m2m":
                    values[n] = m2m.get(n, [])
                elif kind in ("image", "file"):
                    values[n] = (getattr(obj, n) if obj is not None else None) or ""
                else:
                    values[n] = str(form.get(n, ""))
            return values

        async with self.session() as s:
            obj = await self._get(s, ma, pk, request, "change") if pk is not None else None
            is_new = obj is None
            data, m2m, files, errors = {}, {}, {}, {}
            for name in ma.form_fields:
                f = ma.fields[name]
                if f.readonly:
                    continue
                if f.kind == "m2m":
                    m2m[name] = [str(v) for v in form.getlist(name) if v]
                elif f.kind in ("image", "file"):
                    upload = form.get(name)
                    if getattr(upload, "filename", None):
                        # ponytail: max_upload is checked after Starlette has spooled the body; cap request size at the proxy too.
                        content = await upload.read()
                        try:
                            check_upload(f.kind, upload.filename, content, self.max_upload)
                            files[name] = (extension(upload.filename), content, upload.content_type)
                        except FieldError as e:
                            errors[name] = str(e)
                    elif form.get(f"{name}__clear"):
                        files[name] = None
                    current = None if is_new else getattr(obj, name)
                    if f.required and name not in errors and files.get(name, current) is None:
                        errors[name] = "This field is required."
                else:
                    try:
                        data[name] = f.parse(form.get(name))
                    except FieldError as e:
                        errors[name] = str(e)
            for name, value in data.items():
                f = ma.fields[name]
                if f.kind == "fk" and f.target and value is not None and await self._objects(s, f.target, [str(value)], request) is None:
                    errors[name] = "Select a valid option."
            targets = {}
            for name, ids in m2m.items():
                found = await self._objects(s, ma.fields[name].target, ids, request)
                if found is None:
                    errors[name] = "Select a valid option."
                else:
                    targets[name] = found
            rows = {inl.name: self._inline_rows(form, inl, errors) for inl in ma.inlines}
            if not errors and ma.schema is not None:
                try:
                    valid = ma.schema.model_validate(data)
                    data.update({k: getattr(valid, k) for k in data if hasattr(valid, k)})
                except ValidationError as e:
                    for err in e.errors():
                        errors[str(err["loc"][0]) if err["loc"] else "__all__"] = err["msg"]
            if errors:
                return await self._form(request, s, ma, user, obj, posted(obj), errors, 422, inline_rows=rows)

            obj = obj if obj is not None else ma.model()
            changes = self._assign(ma, obj, data, targets, is_new)
            for name, upload in files.items():
                old = getattr(obj, name)
                key = None if upload is None else await self.storage.save(f"{ma.slug}/{secrets.token_hex(16)}{upload[0]}", upload[1], upload[2])
                if key:
                    fresh.append(key)
                if key != old:
                    f = ma.fields[name]
                    changes[name] = [f.display(old) if old else None, f.display(key) if key else None]
                    setattr(obj, name, key)
                    if old:
                        stale.append(old)
            for inl in ma.inlines:
                if summary := await self._apply_inline(s, obj, inl, rows[inl.name]):
                    changes[inl.name] = [None, summary]
            try:
                await call_hook(ma.save, s, obj, is_new)
                await s.flush()
                audit.record(s, user=user, action="add" if is_new else "change", model=ma.slug, obj_id=ma.pk_of(obj),
                             obj_repr=ma.label(obj), changes=changes)
                await s.commit()
            except IntegrityError as e:
                # A unique or foreign-key constraint said no: keep the user's input and explain, instead of a 500.
                await s.rollback()
                for key in fresh:
                    await self.storage.delete(key)
                obj = await self._get(s, ma, pk, request, "change") if pk is not None else None
                errors = {"__all__": f"Couldn't save: {db_message(e)}"}
                return await self._form(request, s, ma, user, obj, posted(obj), errors, 422, inline_rows=rows)
            new_pk = ma.pk_of(obj)
        for key in stale:
            await self.storage.delete(key)
        await call_hook(ma.after_save, obj, is_new)
        label = ma.label(obj)
        if form.get("_continue"):
            return RedirectResponse(self.url(ma.slug, new_pk, msg=f"Saved {label}"), 303)
        if form.get("_another"):
            return RedirectResponse(self.url(ma.slug, "new", msg=f"Saved {label}"), 303)
        return RedirectResponse(self.url(ma.slug, msg=f"Saved {label}"), 303)
