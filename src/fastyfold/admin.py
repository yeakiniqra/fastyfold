"""Admin: model registry, auth, and every HTTP view."""

import inspect as pyinspect
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from . import audit
from .fields import FieldError
from .model import ModelAdmin
from .security import SESSION_COOKIE, SESSION_MAX_AGE, Signer

HERE = Path(__file__).parent

Authenticate = Callable[[str, str], Awaitable[str | None]]
Permission = Callable[[str, str, ModelAdmin], bool]


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
        self.models: dict[str, ModelAdmin] = {}
        self.prefix = ""
        self._log_ready = False
        self.templates = Jinja2Templates(directory=HERE / "templates")
        self.templates.env.globals["admin"] = self
        self.templates.env.globals["tone"] = lambda text: sum(map(ord, str(text or ""))) % 5
        self.router = APIRouter()
        self._routes()

    # ── setup ─────────────────────────────────────────────────────────────
    def register(self, cls: type[ModelAdmin]) -> type[ModelAdmin]:
        """Register a ModelAdmin subclass. Usable as a decorator."""
        ma = cls(self)
        if ma.slug in self.models or ma.slug.startswith("_") or ma.slug in ("login", "logout", "static"):
            raise ValueError(f"fastyfold: slug {ma.slug!r} is taken or reserved")
        self.models[ma.slug] = ma
        return cls

    def mount(self, app: FastAPI, path: str = "/admin") -> None:
        self.prefix = path.rstrip("/")
        # Static first, so /admin/static/... never reaches the /{model}/{pk} route.
        app.mount(f"{self.prefix}/static", StaticFiles(directory=HERE / "static"), name="fastyfold-static")
        app.include_router(self.router, prefix=self.prefix)

    @asynccontextmanager
    async def session(self):
        if not self._log_ready:
            await audit.ensure_table(self.engine)
            self._log_ready = True
        async with self.sessionmaker() as s:
            yield s

    def url(self, *parts, **query) -> str:
        parts = [str(p) for p in parts if p != ""]
        path = "/".join([self.prefix, *parts]) if parts else self.prefix + "/"
        query = {k: v for k, v in query.items() if v not in (None, "")}
        return path + ("?" + urlencode(query, doseq=True) if query else "")

    def _local(self, url: str, default: str) -> str:
        """`url` if it stays inside the admin, else `default`. Blocks `//host` and `/\\host`."""
        inside = url.startswith(self.prefix + "/") and not url.startswith(("//", "/\\"))
        return url if inside else default

    def can(self, user: str, perm: str, ma: ModelAdmin) -> bool:
        if self.permission and not self.permission(user, perm, ma):
            return False
        return ma.has_permission(user, perm)

    def groups(self, user: str) -> list[dict]:
        """Models the user may view, grouped for the rail and context panel."""
        out: dict[str, dict] = {}
        for ma in self.models.values():
            if not self.can(user, "view", ma):
                continue
            g = out.setdefault(ma.group, {"name": ma.group, "icon": self.group_icons.get(ma.group, ma.icon), "models": []})
            g["models"].append(ma)
        return list(out.values())

    # ── request helpers ───────────────────────────────────────────────────
    def _user(self, request: Request) -> str:
        user = self.signer.load(request.cookies.get(SESSION_COOKIE))
        if user:
            return user
        login = self.url("login", next=str(request.url.path))
        if request.headers.get("HX-Request"):
            raise HTTPException(401, headers={"HX-Redirect": login})
        raise HTTPException(303, headers={"Location": login})

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
        cookie = request.cookies.get(SESSION_COOKIE)
        ctx.setdefault("user", self.signer.load(cookie))
        ctx.setdefault("current", None)
        ctx["csrf"] = self.signer.csrf(cookie) if cookie else ""
        ctx["nav"] = self.groups(ctx["user"]) if ctx["user"] else []
        ctx["jumps"] = {"d": self.url(""), **{m.key.lower(): self.url(m.slug) for g in ctx["nav"] for m in g["models"] if m.key}}
        ctx["msg"] = request.query_params.get("msg", "")
        return self.templates.TemplateResponse(request, f"fastyfold/{template}", ctx, status_code=status)

    @staticmethod
    def _greeting() -> str:
        h = datetime.now().hour
        return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"

    # ── routes ────────────────────────────────────────────────────────────
    def _routes(self) -> None:
        r = self.router
        User = Depends(self._user)
        Csrf = Depends(self._csrf)

        @r.get("/login", response_class=HTMLResponse, include_in_schema=False)
        async def login_page(request: Request, next: str = ""):
            return self.render(request, "login.html", next=next, user=None)

        @r.post("/login", include_in_schema=False)
        async def login(request: Request):
            form = await request.form()
            username, password = str(form.get("username", "")), str(form.get("password", ""))
            next_url = str(form.get("next", ""))
            user = await self.authenticate(username, password)
            if not user:
                return self.render(request, "login.html", 401, next=next_url, user=None, error="That username and password didn't match.", username=username)
            resp = RedirectResponse(self._local(next_url, self.url("")), 303)
            resp.set_cookie(SESSION_COOKIE, self.signer.dump(user), max_age=SESSION_MAX_AGE if form.get("remember") else None,
                            httponly=True, samesite="lax", secure=request.url.scheme == "https", path=self.prefix or "/")
            return resp

        @r.post("/logout", include_in_schema=False, dependencies=[Csrf])
        async def logout(user: str = User):
            resp = RedirectResponse(self.url("login"), 303)
            resp.delete_cookie(SESSION_COOKIE, path=self.prefix or "/")
            return resp

        @r.get("/", response_class=HTMLResponse, include_in_schema=False)
        async def dashboard(request: Request, user: str = User):
            visible = [ma for g in self.groups(user) for ma in g["models"]]
            async with self.session() as s:
                counts = {ma.slug: await ma.count(s) for ma in visible}
                lead = next((ma for ma in visible if ma.board_field), visible[0] if visible else None)
                mix = await lead.board_counts(s) if lead and lead.board_field else {}
                latest = (await s.scalars(lead.base_query().order_by(getattr(lead.model, lead.pk).desc()).limit(5))).all() if lead else []
                activity = await audit.recent(s, limit=6)
            return self.render(request, "dashboard.html", user=user, models=visible, counts=counts, lead=lead,
                               mix=mix, latest=latest, activity=activity, greeting=self._greeting())

        @r.get("/_search", include_in_schema=False)
        async def search(q: str = "", user: str = User):
            q = q.strip()
            groups = []
            visible = [ma for g in self.groups(user) for ma in g["models"]]
            if q:
                records = []
                async with self.session() as s:
                    for ma in visible:
                        if not ma.search_fields:
                            continue
                        for obj in (await s.scalars(ma.list_query(q=q).limit(3))).all():
                            sub = ma.subtitle(obj) or f"{ma.name} #{ma.pk_of(obj)}"
                            records.append({"label": ma.label(obj), "sub": sub, "url": self.url(ma.slug, ma.pk_of(obj)), "icon": ma.icon})
                groups.append({"title": "Records", "items": records[:8]})
            ql = q.lower()
            groups.append({"title": "Models", "items": [
                {"label": ma.name_plural, "sub": f"Go to {ma.name_plural.lower()}", "url": self.url(ma.slug), "icon": ma.icon,
                 "keys": ["G", ma.key.upper()] if ma.key else None}
                for ma in visible if not q or ql in ma.name_plural.lower()]})
            actions = [{"label": f"New {ma.name.lower()}", "sub": ma.name_plural, "url": self.url(ma.slug, "new"), "icon": "add"}
                       for ma in visible if self.can(user, "add", ma) and (not q or ql in f"new {ma.name.lower()}")]
            for label, icon, cmd, keys in (("Toggle dark mode", "dark_mode", "theme", ["T"]), ("Keyboard shortcuts", "keyboard", "keys", ["?"]),
                                           ("Audit log", "history", None, None)):
                if not q or ql in label.lower():
                    actions.append({"label": label, "icon": icon, "cmd": cmd, "keys": keys, "url": self.url("_log") if not cmd else None, "sub": "fastyfold"})
            groups.append({"title": "Actions", "items": actions})
            return JSONResponse({"groups": [g for g in groups if g["items"]]})

        @r.get("/_log", response_class=HTMLResponse, include_in_schema=False)
        async def log(request: Request, page: int = 1, user: str = User):
            async with self.session() as s:
                entries = await audit.recent(s, limit=51, offset=(max(page, 1) - 1) * 50)
            return self.render(request, "log.html", user=user, entries=entries[:50], page=page, has_more=len(entries) > 50)

        @r.get("/{slug}", response_class=HTMLResponse, include_in_schema=False)
        async def list_view(request: Request, slug: str, user: str = User):
            ma = self._model(slug, user)
            p = request.query_params
            view = p.get("view") or ("board" if ma.board_field else "table")
            if view == "board" and not ma.board_field:
                view = "table"
            q = p.get("q", "")
            filters = {f["name"]: p.get(f"f_{f['name']}", "") for f in ma.filter_options()}
            sort = p.get("sort") or None
            raw_page = p.get("page", "")
            page = max(1, int(raw_page)) if raw_page.isdigit() else 1
            stmt = ma.list_query(q=q, filters=filters, sort=sort)
            async with self.session() as s:
                total = await ma.count(s, stmt)
                per = ma.per_page * (4 if view == "board" else 1)
                # ponytail: OFFSET paging slows down past ~100k rows; switch to keyset paging on the sort column.
                rows = (await s.scalars(stmt.limit(per).offset((page - 1) * per))).all()
                counts = await ma.board_counts(s)
            state = {"view": view, "q": q, "sort": sort, "page": page, **{f"f_{k}": v for k, v in filters.items()}}
            return self.render(request, "list.html", user=user, ma=ma, current=ma, rows=rows, total=total, page=page,
                               pages=max(1, -(-total // per)), view=view, q=q, filters=filters, sort=sort or "",
                               counts=counts, state=state, active_filters={k: v for k, v in filters.items() if v})

        @r.get("/{slug}/new", response_class=HTMLResponse, include_in_schema=False)
        async def new_form(request: Request, slug: str, user: str = User):
            ma = self._model(slug, user, "add")
            async with self.session() as s:
                return await self._form(request, s, ma, user, None, {}, {})

        @r.post("/{slug}/new", include_in_schema=False, dependencies=[Csrf])
        async def create(request: Request, slug: str, user: str = User):
            ma = self._model(slug, user, "add")
            return await self._save(request, ma, user, None)

        @r.post("/{slug}/action", include_in_schema=False, dependencies=[Csrf])
        async def run_action(request: Request, slug: str, user: str = User):
            form = await request.form()
            name = str(form.get("action", ""))
            ma = self._model(slug, user, "delete" if name == "delete" else name)
            if name != "delete" and name not in ma.actions:
                raise HTTPException(400, "Unknown action")
            ids = [i for i in form.getlist("ids") if i]
            if not ids:
                return RedirectResponse(self.url(ma.slug, msg="Select at least one row first"), 303)
            try:
                keys = [ma.fields[ma.pk].parse(i) for i in ids]
            except FieldError:
                raise HTTPException(400, "Invalid id") from None
            async with self.session() as s:
                objs = (await s.scalars(ma.base_query().where(getattr(ma.model, ma.pk).in_(keys)))).all()
                if name == "delete":
                    for o in objs:
                        audit.record(s, user=user, action="delete", model=ma.slug, obj_id=ma.pk_of(o), obj_repr=ma.label(o))
                        await ma.delete(s, o)
                    label = "Deleted"
                else:
                    await getattr(ma, name)(s, objs)
                    for o in objs:
                        audit.record(s, user=user, action=name, model=ma.slug, obj_id=ma.pk_of(o), obj_repr=ma.label(o))
                    label = ma.actions[name]["label"]
                await s.commit()
            back = self._local(str(form.get("next", "")), self.url(ma.slug))
            sep = "&" if "?" in back else "?"
            return RedirectResponse(f"{back}{sep}{urlencode({'msg': f'{label}: {len(objs)} {ma.name_plural.lower()}'})}", 303)

        @r.get("/{slug}/{pk}/preview", response_class=HTMLResponse, include_in_schema=False)
        async def preview(request: Request, slug: str, pk: str, user: str = User):
            ma = self._model(slug, user)
            async with self.session() as s:
                obj = await self._get(s, ma, pk)
                history = await audit.recent(s, model=ma.slug, object_id=pk, limit=5)
            return self.render(request, "partials/preview.html", user=user, ma=ma, obj=obj, history=history)

        @r.get("/{slug}/{pk}", response_class=HTMLResponse, include_in_schema=False)
        async def edit_form(request: Request, slug: str, pk: str, user: str = User):
            ma = self._model(slug, user)
            async with self.session() as s:
                obj = await self._get(s, ma, pk)
                return await self._form(request, s, ma, user, obj, {}, {})

        @r.post("/{slug}/{pk}", include_in_schema=False, dependencies=[Csrf])
        async def update(request: Request, slug: str, pk: str, user: str = User):
            ma = self._model(slug, user, "change")
            return await self._save(request, ma, user, pk)

        @r.post("/{slug}/{pk}/delete", include_in_schema=False, dependencies=[Csrf])
        async def delete(slug: str, pk: str, user: str = User):
            ma = self._model(slug, user, "delete")
            async with self.session() as s:
                obj = await self._get(s, ma, pk)
                label = ma.label(obj)
                await ma.delete(s, obj)
                audit.record(s, user=user, action="delete", model=ma.slug, obj_id=pk, obj_repr=label)
                await s.commit()
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
                obj = await self._get(s, ma, pk)
                old = ma.raw(obj, ma.board_field)
                setattr(obj, ma.board_field, value)
                audit.record(s, user=user, action="change", model=ma.slug, obj_id=pk, obj_repr=ma.label(obj),
                             changes={ma.board_field: [old, ma.raw(obj, ma.board_field)]})
                await s.commit()
            return Response(status_code=204)

    # ── form helpers ──────────────────────────────────────────────────────
    async def _get(self, s, ma: ModelAdmin, pk: str):
        try:
            key = ma.fields[ma.pk].parse(pk)
        except FieldError:
            raise HTTPException(404, "Not found") from None
        obj = await s.scalar(ma.base_query().where(getattr(ma.model, ma.pk) == key))
        if obj is None:
            raise HTTPException(404, f"{ma.name} not found")
        return obj

    async def _form(self, request, s, ma: ModelAdmin, user: str, obj, values: dict, errors: dict, status: int = 200):
        fk_options = {n: await ma.fk_options(s, ma.fields[n]) for n in ma.form_fields if ma.fields[n].kind == "fk" and ma.fields[n].target}
        if not values:
            values = {n: ma.fields[n].form_value(getattr(obj, n) if obj is not None else ma.fields[n].default) for n in ma.form_fields}
        history = await audit.recent(s, model=ma.slug, object_id=ma.pk_of(obj), limit=6) if obj is not None else []
        return self.render(request, "form.html", status, user=user, ma=ma, current=ma, obj=obj, values=values, errors=errors,
                           fk_options=fk_options, history=history, can_change=self.can(user, "change", ma),
                           can_delete=obj is not None and self.can(user, "delete", ma))

    async def _save(self, request: Request, ma: ModelAdmin, user: str, pk: str | None):
        form = await request.form()
        data, errors = {}, {}
        for name in ma.form_fields:
            f = ma.fields[name]
            if f.readonly:
                continue
            try:
                data[name] = f.parse(form.get(name))
            except FieldError as e:
                errors[name] = str(e)
        if not errors and ma.schema is not None:
            try:
                valid = ma.schema.model_validate(data)
                data.update({k: getattr(valid, k) for k in data if hasattr(valid, k)})
            except ValidationError as e:
                for err in e.errors():
                    errors[str(err["loc"][0]) if err["loc"] else "__all__"] = err["msg"]
        async with self.session() as s:
            obj = await self._get(s, ma, pk) if pk is not None else None
            if errors:
                values = {n: str(form.get(n, "")) for n in ma.form_fields}
                return await self._form(request, s, ma, user, obj, values, errors, 422)
            is_new = obj is None
            obj = obj if obj is not None else ma.model()
            changes = {}
            for k, v in data.items():
                old = getattr(obj, k, None)
                if old != v:
                    changes[k] = [ma.fields[k].display(old) if not is_new else None, ma.fields[k].display(v)]
                    setattr(obj, k, v)
            maybe = ma.save(s, obj, is_new)
            if pyinspect.isawaitable(maybe):
                await maybe
            await s.flush()
            audit.record(s, user=user, action="add" if is_new else "change", model=ma.slug, obj_id=ma.pk_of(obj),
                         obj_repr=ma.label(obj), changes=changes)
            await s.commit()
            new_pk = ma.pk_of(obj)
        label = ma.label(obj)
        if form.get("_continue"):
            return RedirectResponse(self.url(ma.slug, new_pk, msg=f"Saved {label}"), 303)
        if form.get("_another"):
            return RedirectResponse(self.url(ma.slug, "new", msg=f"Saved {label}"), 303)
        return RedirectResponse(self.url(ma.slug, msg=f"Saved {label}"), 303)
