"""Built-in users, used when `Admin(...)` gets no `authenticate=`.

The first visit to the admin shows a setup page: type the one-time code printed in the server
console, then create the owner account. Owners can do everything and manage everyone else on the
Users screen: add people, reset passwords, make them owners or give them roles. Everyone can
change their own password.
"""

import secrets
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import Boolean, DateTime, Integer, MetaData, String, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from . import audit, roles
from .security import hash_password, verify_password

if TYPE_CHECKING:
    from .admin import Admin

AUDIT_MODEL = "_users"
MIN_PASSWORD = 8
_DUMMY_HASH = hash_password("timing-equaliser", iterations=1000)

# The signed-in user's name if they are an owner, for this request (set by Admin._signed_in).
current_owner: ContextVar[str | None] = ContextVar("fastyfold_owner", default=None)


class _Base(DeclarativeBase):
    metadata = MetaData()


class User(_Base):
    __tablename__ = "fastyfold_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(150), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


async def ensure_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)


async def authenticate(admin: "Admin", username: str, password: str) -> str | None:
    async with admin.session() as s:
        user = await s.scalar(select(User).where(User.username == username))
        # Hash even for unknown names, so response time doesn't reveal which usernames exist.
        if not verify_password(password, user.password_hash if user else _DUMMY_HASH) or user is None:
            return None
        user.last_login_at = datetime.now(timezone.utc)
        await s.commit()
        return user.username


async def is_owner(session, username: str) -> bool:
    return bool(await session.scalar(select(User.is_owner).where(User.username == username)))


async def needs_setup(admin: "Admin") -> bool:
    """True until the first user exists. Cached once false, since users are never all deleted (the last owner can't be)."""
    if admin._has_users:
        return False
    async with admin.session() as s:
        admin._has_users = bool(await s.scalar(select(func.count()).select_from(User)))
    return not admin._has_users


async def setup_code(admin: "Admin") -> str:
    """The one-time code for the setup page, kept in the database so every worker agrees on it."""
    code = await admin._setting("setup_code")
    if code is None:
        raw = secrets.token_hex(4).upper()
        await admin._set_setting("setup_code", f"{raw[:4]}-{raw[4:]}", only_if_missing=True)
        code = await admin._setting("setup_code")
    return code


def _password_problem(password: str, confirm: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if password != confirm:
        return "The two passwords don't match."
    return None


def build(admin: "Admin") -> APIRouter:
    router = APIRouter(route_class=admin.router.route_class, dependencies=[Depends(admin._boot)])
    Csrf = Depends(admin._csrf)

    async def owner(request: Request) -> str:
        user = await admin._user(request)
        if not admin.is_owner(user):
            raise HTTPException(403, "Only owners can manage users.")
        return user

    Owner = Depends(owner)
    User_ = Depends(admin._user)

    # ── first run ─────────────────────────────────────────────────────────
    @router.get("/setup", response_class=HTMLResponse, include_in_schema=False)
    async def setup_page(request: Request):
        if not await needs_setup(admin):
            return RedirectResponse(admin.url("login"), 303)
        code = await setup_code(admin)
        print(f"\n  fastyfold setup code: {code}   (enter it at {admin.url('setup')} to create the first admin user)\n", flush=True)
        return admin._render(request, "setup.html", user=None, values={}, errors={})

    @router.post("/setup", include_in_schema=False)
    async def setup(request: Request):
        if not await needs_setup(admin):
            return RedirectResponse(admin.url("login"), 303)
        form = await request.form()
        values = {k: str(form.get(k, "")) for k in ("code", "username")}
        password, confirm = str(form.get("password", "")), str(form.get("confirm", ""))
        ip = request.client.host if request.client else "unknown"
        errors = {}
        if admin._blocked(ip):
            errors["code"] = "Too many wrong codes. Try again in 15 minutes."
        elif not secrets.compare_digest(values["code"].strip().upper(), await setup_code(admin)):
            admin._failures.setdefault(ip, []).append(time.monotonic())
            errors["code"] = "That isn't the code in the server console."
        if not values["username"].strip():
            errors["username"] = "This field is required."
        if problem := _password_problem(password, confirm):
            errors["password"] = problem
        if errors:
            return admin._render(request, "setup.html", 422, user=None, values=values, errors=errors)
        username = values["username"].strip()[:150]
        async with admin.session() as s:
            if await s.scalar(select(func.count()).select_from(User)):   # someone finished setup a moment ago
                return RedirectResponse(admin.url("login"), 303)
            s.add(User(username=username, password_hash=hash_password(password), is_owner=True, last_login_at=datetime.now(timezone.utc)))
            audit.record(s, user=username, action="add", model=AUDIT_MODEL, obj_id=username, obj_repr=f"User {username}",
                         changes={"owner": [None, "Yes"]})
            await s.commit()
        await admin._delete_setting("setup_code")
        admin._has_users = True
        return await admin._start_session(request, username, remember=True, next_url=admin.url(""))

    # ── users (owners) ────────────────────────────────────────────────────
    async def all_roles(s):
        return (await s.scalars(select(roles.Role).order_by(roles.Role.name))).all()

    async def roles_of(s, username: str) -> set[int]:
        return set(await s.scalars(select(roles.RoleMember.role_id).where(roles.RoleMember.user == username)))

    def form_page(request, user, target, values, errors, role_list, status=200):
        return admin._render(request, "user_form.html", status, user=user, target=target, values=values, errors=errors,
                             roles=role_list, min_password=MIN_PASSWORD)

    @router.get("/_users", response_class=HTMLResponse, include_in_schema=False)
    async def users(request: Request, user: str = Owner):
        async with admin.session() as s:
            rows = (await s.scalars(select(User).order_by(User.username))).all()
            members = (await s.execute(select(roles.RoleMember.user, roles.Role.name)
                                       .join(roles.Role, roles.Role.id == roles.RoleMember.role_id))).all()
        names: dict[str, list[str]] = {}
        for who, role in members:
            names.setdefault(who, []).append(role)
        return admin._render(request, "users.html", user=user, users=rows, role_names=names)

    @router.get("/_users/new", response_class=HTMLResponse, include_in_schema=False)
    async def new_user(request: Request, user: str = Owner):
        async with admin.session() as s:
            return form_page(request, user, None, {"username": "", "is_owner": False, "roles": set()}, {}, await all_roles(s))

    @router.get("/_users/{user_id:int}", response_class=HTMLResponse, include_in_schema=False)
    async def edit_user(request: Request, user_id: int, user: str = Owner):
        async with admin.session() as s:
            target = await s.get(User, user_id)
            if target is None:
                raise HTTPException(404, "User not found")
            values = {"username": target.username, "is_owner": target.is_owner, "roles": await roles_of(s, target.username)}
            return form_page(request, user, target, values, {}, await all_roles(s))

    async def save_user(request: Request, user: str, user_id: int | None):
        form = await request.form()
        password, confirm = str(form.get("password", "")), str(form.get("confirm", ""))
        values = {"username": str(form.get("username", "")).strip()[:150], "is_owner": bool(form.get("is_owner")),
                  "roles": {int(r) for r in form.getlist("roles") if str(r).isdigit()}}
        errors = {}
        async with admin.session() as s:
            target = await s.get(User, user_id) if user_id is not None else None
            if user_id is not None and target is None:
                raise HTTPException(404, "User not found")
            role_list = await all_roles(s)
            if target is None and not values["username"]:
                errors["username"] = "This field is required."
            elif target is None and await s.scalar(select(User.id).where(User.username == values["username"])):
                errors["username"] = "Someone already has this username."
            if (target is None or password) and (problem := _password_problem(password, confirm)):
                errors["password"] = problem
            if target is not None and target.username == user and not values["is_owner"]:
                errors["is_owner"] = "You can't remove your own owner access."
            if errors:
                return form_page(request, user, target, values, errors, role_list, 422)
            is_new = target is None
            try:
                return await write(s, target, is_new, values, password, role_list, user)
            except IntegrityError:          # two owners added the same name at once
                await s.rollback()
                errors["username"] = "Someone already has this username."
                return form_page(request, user, None, values, errors, role_list, 422)

    async def write(s, target, is_new, values, password, role_list, user):
        if is_new:
            target = User(username=values["username"], password_hash=hash_password(password))
            s.add(target)
        elif password:
            target.password_hash = hash_password(password)
        was_owner = None if is_new else target.is_owner
        demoted = bool(was_owner) and not values["is_owner"]
        target.is_owner = values["is_owner"]
        await s.execute(delete(roles.RoleMember).where(roles.RoleMember.user == target.username))
        s.add_all([roles.RoleMember(role_id=r, user=target.username) for r in sorted(values["roles"] & {r.id for r in role_list})])
        yes_no = {None: None, True: "Yes", False: "No"}
        changes = {"owner": [yes_no[was_owner], yes_no[values["is_owner"]]]} if was_owner != values["is_owner"] else {}
        if password and not is_new:
            changes["password"] = ["…", "changed"]
        audit.record(s, user=user, action="add" if is_new else "change", model=AUDIT_MODEL, obj_id=target.username,
                     obj_repr=f"User {target.username}", changes=changes)
        await s.commit()
        if not is_new and (password or demoted):
            await admin.end_sessions(target.username)      # a reset password or lost access takes effect now
        return RedirectResponse(admin.url("_users", msg=f"Saved {target.username}"), 303)

    @router.post("/_users/new", include_in_schema=False, dependencies=[Csrf])
    async def create_user(request: Request, user: str = Owner):
        return await save_user(request, user, None)

    @router.post("/_users/{user_id:int}", include_in_schema=False, dependencies=[Csrf])
    async def update_user(request: Request, user_id: int, user: str = Owner):
        return await save_user(request, user, user_id)

    @router.post("/_users/{user_id:int}/delete", include_in_schema=False, dependencies=[Csrf])
    async def delete_user(request: Request, user_id: int, user: str = Owner):
        async with admin.session() as s:
            target = await s.get(User, user_id)
            if target is None:
                raise HTTPException(404, "User not found")
            if target.username == user:
                return RedirectResponse(admin.url("_users", user_id, msg="You can't delete yourself."), 303)
            name = target.username
            await s.execute(delete(roles.RoleMember).where(roles.RoleMember.user == name))
            await s.delete(target)
            audit.record(s, user=user, action="delete", model=AUDIT_MODEL, obj_id=name, obj_repr=f"User {name}")
            await s.commit()
        await admin.end_sessions(name)
        return RedirectResponse(admin.url("_users", msg=f"Deleted {name}"), 303)

    # ── my account ────────────────────────────────────────────────────────
    @router.get("/_account", response_class=HTMLResponse, include_in_schema=False)
    async def account(request: Request, user: str = User_):
        return admin._render(request, "account.html", user=user, errors={}, min_password=MIN_PASSWORD)

    @router.post("/_account", include_in_schema=False, dependencies=[Csrf])
    async def change_password(request: Request, user: str = User_):
        form = await request.form()
        current, password, confirm = (str(form.get(k, "")) for k in ("current", "password", "confirm"))
        errors = {}
        async with admin.session() as s:
            me = await s.scalar(select(User).where(User.username == user))
            if me is None:
                raise HTTPException(404, "Your account lives outside fastyfold.")
            if not verify_password(current, me.password_hash):
                errors["current"] = "That isn't your current password."
            if problem := _password_problem(password, confirm):
                errors["password"] = problem
            if errors:
                return admin._render(request, "account.html", 422, user=user, errors=errors, min_password=MIN_PASSWORD)
            me.password_hash = hash_password(password)
            audit.record(s, user=user, action="change", model=AUDIT_MODEL, obj_id=user, obj_repr=f"User {user}",
                         changes={"password": ["…", "changed"]})
            await s.commit()
        await admin.end_sessions(user)              # sign out every other browser too
        return await admin._start_session(request, user, remember=True, next_url=admin.url("", msg="Password changed"))

    return router
