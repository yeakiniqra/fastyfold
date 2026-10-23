"""Roles: a ready-made permission editor. Turned on with `Admin(owners=["you"])`.

Owners can do everything and are the only ones who manage roles. Everyone else can do exactly what
their roles grant: a grant is (model or page slug, permission), where the permission is view, add,
change, delete, export or an @action name. Roles stack with has_permission, has_object_permission and
Admin(permission=...): every one of them has to allow.
"""

from contextvars import ContextVar
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import ForeignKey, Integer, MetaData, String, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload

from . import audit, users

if TYPE_CHECKING:
    from .admin import Admin

BASE_PERMS = ("view", "add", "change", "delete", "export")
AUDIT_MODEL = "_roles"

# The signed-in user's grants for this request, loaded once by Admin._user so permission checks stay synchronous.
current_grants: ContextVar[frozenset[tuple[str, str]] | None] = ContextVar("fastyfold_grants", default=None)


class _Base(DeclarativeBase):
    metadata = MetaData()


class Role(_Base):
    __tablename__ = "fastyfold_role"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(200), default="")
    grants: Mapped[list["RoleGrant"]] = relationship(cascade="all, delete-orphan", lazy="raise")
    members: Mapped[list["RoleMember"]] = relationship(cascade="all, delete-orphan", lazy="raise")


class RoleGrant(_Base):
    __tablename__ = "fastyfold_role_grant"

    role_id: Mapped[int] = mapped_column(ForeignKey("fastyfold_role.id", ondelete="CASCADE"), primary_key=True)
    model: Mapped[str] = mapped_column(String(100), primary_key=True)
    perm: Mapped[str] = mapped_column(String(60), primary_key=True)


class RoleMember(_Base):
    __tablename__ = "fastyfold_role_member"

    role_id: Mapped[int] = mapped_column(ForeignKey("fastyfold_role.id", ondelete="CASCADE"), primary_key=True)
    user: Mapped[str] = mapped_column(String(150), primary_key=True, index=True)


async def ensure_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)


async def grants_for(session, user: str) -> frozenset[tuple[str, str]]:
    rows = await session.execute(select(RoleGrant.model, RoleGrant.perm)
                                 .join(RoleMember, RoleMember.role_id == RoleGrant.role_id).where(RoleMember.user == user))
    return frozenset((m, p) for m, p in rows)


def matrix(admin: "Admin") -> list[dict]:
    """Rows of the permission editor: every model and page with the permissions it understands."""
    rows = [{"slug": ma.slug, "label": ma.name_plural, "icon": ma.icon, "group": ma.group,
             "perms": list(BASE_PERMS), "actions": [(a["name"], a["label"]) for a in ma.actions.values()]}
            for ma in admin.models.values()]
    rows += [{"slug": p.slug, "label": p.name, "icon": p.icon, "group": p.group, "perms": ["view"], "actions": []}
             for p in admin.pages.values()]
    for r in rows:
        r["grant_keys"] = [f"{r['slug']}:{p}" for p in [*r["perms"], *(a for a, _ in r["actions"])]]
    return rows


def _valid_grants(admin: "Admin") -> set[str]:
    return {k for r in matrix(admin) for k in r["grant_keys"]}


def parse_members(raw: str) -> list[str]:
    """One user per line or comma; trimmed, de-duplicated, order kept."""
    out: list[str] = []
    for part in raw.replace(",", "\n").splitlines():
        user = part.strip()[:150]
        if user and user not in out:
            out.append(user)
    return out


def build(admin: "Admin") -> APIRouter:
    router = APIRouter(prefix="/_roles", route_class=admin.router.route_class, dependencies=[Depends(admin._boot)])

    async def owner(request: Request) -> str:
        user = await admin._user(request)
        if not admin.is_owner(user):
            raise HTTPException(403, "Only owners can manage roles.")
        return user

    Owner = Depends(owner)
    Csrf = Depends(admin._csrf)

    async def known_users(s) -> list[str]:
        """Suggestions for the members box: everyone who has signed in or appears in the log."""
        names = set(await s.scalars(select(audit.AdminSession.user).distinct()))
        names |= set(await s.scalars(select(audit.LogEntry.user).distinct().limit(500)))
        if admin.builtin_users:
            names |= set(await s.scalars(select(users.User.username).where(users.User.is_owner.is_(False))))
            names -= set(await s.scalars(select(users.User.username).where(users.User.is_owner.is_(True))))
        return sorted(names - set(admin.owners))

    async def load(s, role_id: int) -> Role:
        role = await s.scalar(select(Role).where(Role.id == role_id).options(selectinload(Role.grants), selectinload(Role.members)))
        if role is None:
            raise HTTPException(404, "Role not found")
        return role

    def form_page(request, user, role, values, errors, status=200, suggestions=()):
        rows = matrix(admin)
        columns = {p: [f"{r['slug']}:{p}" for r in rows if p in r["perms"]] for p in BASE_PERMS}
        return admin._render(request, "role_form.html", status, user=user, role=role, values=values, errors=errors,
                             matrix=rows, columns=columns, suggestions=suggestions, perms=BASE_PERMS)

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    async def roles(request: Request, user: str = Owner):
        async with admin.session() as s:
            rows = (await s.scalars(select(Role).order_by(Role.name)
                                    .options(selectinload(Role.grants), selectinload(Role.members)))).all()
        people: dict[str, list[str]] = {}
        for r in rows:
            for m in r.members:
                people.setdefault(m.user, []).append(r.name)
        return admin._render(request, "roles.html", user=user, roles=rows, people=dict(sorted(people.items())))

    @router.get("/new", response_class=HTMLResponse, include_in_schema=False)
    async def new(request: Request, user: str = Owner):
        async with admin.session() as s:
            return form_page(request, user, None, {"name": "", "description": "", "members": "", "grants": set()}, {},
                             suggestions=await known_users(s))

    @router.get("/{role_id:int}", response_class=HTMLResponse, include_in_schema=False)
    async def edit(request: Request, role_id: int, user: str = Owner):
        async with admin.session() as s:
            role = await load(s, role_id)
            values = {"name": role.name, "description": role.description, "members": "\n".join(m.user for m in role.members),
                      "grants": {f"{g.model}:{g.perm}" for g in role.grants}}
            return form_page(request, user, role, values, {}, suggestions=await known_users(s))

    async def save(request: Request, user: str, role_id: int | None):
        form = await request.form()
        valid = _valid_grants(admin)
        values = {"name": str(form.get("name", "")).strip(), "description": str(form.get("description", "")).strip()[:200],
                  "members": str(form.get("members", "")), "grants": {g for g in form.getlist("grant") if g in valid}}
        members = parse_members(values["members"])
        errors = {}
        if not values["name"]:
            errors["name"] = "This field is required."
        elif len(values["name"]) > 80:
            errors["name"] = "Keep it under 80 characters."
        async with admin.session() as s:
            role = await load(s, role_id) if role_id is not None else None
            if errors:
                return form_page(request, user, role, values, errors, 422, suggestions=await known_users(s))
            is_new = role is None
            old = (None, None, None) if is_new else (role.name, sorted(m.user for m in role.members), len(role.grants))
            role = role or Role()
            role.name, role.description = values["name"], values["description"]
            role.members = [RoleMember(user=u) for u in members]
            role.grants = [RoleGrant(model=g.split(":", 1)[0], perm=g.split(":", 1)[1]) for g in sorted(values["grants"])]
            s.add(role)
            try:
                await s.flush()
                changes = {"name": [old[0], role.name], "members": [", ".join(old[1]) if old[1] is not None else None, ", ".join(members) or "—"],
                           "permissions": [old[2], len(values["grants"])]}
                audit.record(s, user=user, action="add" if is_new else "change", model=AUDIT_MODEL, obj_id=role.id,
                             obj_repr=f"Role {role.name}", changes={k: v for k, v in changes.items() if v[0] != v[1]})
                await s.commit()
            except IntegrityError:
                await s.rollback()
                errors["name"] = "Another role already has this name."
                role = await load(s, role_id) if role_id is not None else None
                return form_page(request, user, role, values, errors, 422, suggestions=await known_users(s))
        return RedirectResponse(admin.url("_roles", msg=f"Saved role {values['name']}"), 303)

    @router.post("/new", include_in_schema=False, dependencies=[Csrf])
    async def create(request: Request, user: str = Owner):
        return await save(request, user, None)

    @router.post("/{role_id:int}", include_in_schema=False, dependencies=[Csrf])
    async def update(request: Request, role_id: int, user: str = Owner):
        return await save(request, user, role_id)

    @router.post("/{role_id:int}/delete", include_in_schema=False, dependencies=[Csrf])
    async def delete(request: Request, role_id: int, user: str = Owner):
        async with admin.session() as s:
            role = await load(s, role_id)
            name = role.name
            await s.delete(role)
            audit.record(s, user=user, action="delete", model=AUDIT_MODEL, obj_id=role_id, obj_repr=f"Role {name}")
            await s.commit()
        return RedirectResponse(admin.url("_roles", msg=f"Deleted role {name}"), 303)

    return router
