"""JSON API: typed CRUD routes per model, built from the same ModelAdmin as the HTML views.

Turned on with `Admin(api=True)`. Every route goes through the same permissions, get_query scoping,
validation, hooks and audit log as the forms, and shows up in the host app's OpenAPI docs.
"""

import enum
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ValidationError, create_model
from sqlalchemy.exc import IntegrityError

from . import audit
from .fields import Field, FieldError
from .model import ModelAdmin, call_hook, db_message
from .security import SESSION_COOKIE

if TYPE_CHECKING:
    from .admin import Admin

PY_TYPES: dict[str, Any] = {"int": int, "decimal": Decimal, "str": str, "text": str, "bool": bool, "date": date,
                            "datetime": datetime, "time": time, "uuid": uuid.UUID, "json": Any, "image": str, "file": str}
MAX_PER_PAGE = 100


def _type(f: Field, key_types: dict[type, Any]):
    if f.kind == "choice":
        return Literal[tuple(v for v, _ in f.choices)] if f.choices else str
    if f.kind == "fk":
        return PY_TYPES.get(f.key_kind, Any)
    if f.kind == "m2m":
        return list[key_types.get(f.target, Any)]
    return PY_TYPES.get(f.kind, Any)


def schemas(ma: ModelAdmin, key_types: dict[type, Any]) -> tuple[type[BaseModel], type[BaseModel], type[BaseModel], type[BaseModel]]:
    """(Read, Create, Update, Page) Pydantic models for one ModelAdmin."""
    name = ma.model.__name__
    readable = {n: f for n, f in ma.fields.items() if f.kind != "other"}
    read = create_model(f"{name}Read", **{n: (_type(f, key_types) | None, None) for n, f in readable.items()})
    writable = {n: ma.fields[n] for n in ma.form_fields if ma.fields[n].kind not in ("image", "file", "other") and not ma.fields[n].readonly}
    create = create_model(f"{name}Create", **{
        n: (_type(f, key_types), ...) if f.required and f.default is None else
           (_type(f, key_types) | None, f.default.value if isinstance(f.default, enum.Enum) else f.default)
        for n, f in writable.items()})
    update = create_model(f"{name}Update", **{n: (_type(f, key_types) | None, None) for n, f in writable.items()})
    page = create_model(f"{name}Page", items=(list[read], ...), total=(int, ...), page=(int, ...), per_page=(int, ...))
    return read, create, update, page


def read_dict(ma: ModelAdmin, obj, key_of) -> dict:
    out = {}
    for n, f in ma.fields.items():
        if f.kind == "other":
            continue
        v = getattr(obj, n)
        if f.kind == "m2m":
            v = [key_of(o) for o in v]
        elif isinstance(v, enum.Enum):
            v = v.value
        out[n] = v
    return out


def build(admin: "Admin") -> APIRouter:
    router = APIRouter(prefix="/api", route_class=admin.router.route_class)

    async def session_user(request: Request) -> str:
        try:
            return await admin._user(request)
        except HTTPException:
            raise HTTPException(401, "Sign in first, or call the API with your own credentials (Admin(api_auth=...)).") from None

    async def csrf(request: Request) -> None:
        """Cookie sessions need the CSRF header on writes; api_auth credentials don't ride on cookies, so they don't."""
        if admin.api_auth is None and not admin.signer.csrf_ok(request.cookies.get(SESSION_COOKIE), request.headers.get("X-CSRF-Token")):
            raise HTTPException(403, "Missing or wrong X-CSRF-Token header.")

    auth = admin.api_auth or session_user

    async def current(request: Request, user: str = Depends(auth)) -> str:
        if getattr(request.state, "admin_user", None) != user:
            await admin._signed_in(request, user)     # api_auth users: remember them and load their role grants
        return user

    User = Depends(current)
    key_types = {ma.model: PY_TYPES.get(admin._key_field(ma.model).kind, Any) for ma in admin.models.values()}
    for ma in admin.models.values():
        _routes(admin, router, ma, schemas(ma, key_types), User, [Depends(csrf)])
    return router


def _routes(admin: "Admin", router: APIRouter, ma: ModelAdmin, models, User, write_deps) -> None:
    Read, Create, Update, Page = models
    tag = f"admin · {ma.name_plural}"
    path = f"/{ma.slug}"
    key_of = lambda o: getattr(o, admin._key_field(type(o)).name)  # noqa: E731

    def need(user: str, perm: str, obj=None) -> None:
        if not admin.can(user, perm, ma, obj):
            raise HTTPException(403, "You don't have permission to do that.")

    async def fetch(s, request: Request, pk: str, perm: str):
        need(request.state.admin_user, perm)
        return await admin._get(s, ma, pk, request, perm)

    async def write(s, request: Request, obj, body: BaseModel, is_new: bool):
        user = request.state.admin_user
        given = body.model_dump(exclude_unset=not is_new)
        data, targets, errors = {}, {}, []
        for name, value in given.items():
            f = ma.fields[name]
            if value is None:
                if f.required:
                    errors.append({"loc": ["body", name], "msg": "This field is required.", "type": "missing"})
                elif f.kind == "m2m":
                    targets[name] = []
                else:
                    data[name] = None
                continue
            if f.kind == "m2m":
                found = await admin._objects(s, f.target, [str(v) for v in value], request)
                if found is None:
                    errors.append({"loc": ["body", name], "msg": "Select a valid option.", "type": "value_error"})
                else:
                    targets[name] = found
                continue
            if f.kind in ("choice", "fk"):
                try:
                    value = f.parse(str(value))
                except FieldError as e:
                    errors.append({"loc": ["body", name], "msg": str(e), "type": "value_error"})
                    continue
                if f.kind == "fk" and f.target and await admin._objects(s, f.target, [str(value)], request) is None:
                    errors.append({"loc": ["body", name], "msg": "Select a valid option.", "type": "value_error"})
                    continue
            data[name] = value
        if not errors and ma.schema is not None:
            # A PATCH sends only what changes; the schema checks the record as it would be after the change.
            current = {} if obj is None else {n: getattr(obj, n) for n in Update.model_fields if ma.fields[n].kind != "m2m"}
            try:
                valid = ma.schema.model_validate({**current, **data})
                data.update({k: getattr(valid, k) for k in data if hasattr(valid, k)})
            except ValidationError as e:
                errors += [{"loc": ["body", *map(str, err["loc"])], "msg": err["msg"], "type": err["type"]} for err in e.errors()]
        if errors:
            raise HTTPException(422, errors)
        obj = obj if obj is not None else ma.model()
        changes = admin._assign(ma, obj, data, targets, is_new)
        try:
            await call_hook(ma.save, s, obj, is_new)
            await s.flush()
            audit.record(s, user=user, action="add" if is_new else "change", model=ma.slug, obj_id=ma.pk_of(obj),
                         obj_repr=ma.label(obj), changes=changes)
            await s.commit()
        except IntegrityError as e:
            await s.rollback()
            raise HTTPException(409, db_message(e)) from None
        await call_hook(ma.after_save, obj, is_new)
        # Reload, so columns the database filled (server defaults) are present.
        fresh = (await s.execute(ma.base_query().where(getattr(ma.model, ma.pk) == ma.pk_of(obj))
                                 .execution_options(populate_existing=True))).scalar_one()
        return Read(**read_dict(ma, fresh, key_of))

    @router.get(path, response_model=Page, tags=[tag], summary=f"List {ma.name_plural.lower()}",
                description="Search with `q`, sort with `sort=field` or `sort=-field`, filter with `f_<field>=value` as in the admin list.")
    async def list_rows(request: Request, q: str = "", sort: str | None = None, page: int = Query(1, ge=1),
                        per_page: int = Query(ma.per_page, ge=1, le=MAX_PER_PAGE), user: str = User):
        need(user, "view")
        _, filters, _ = admin._list_args(ma, request.query_params)
        stmt = ma.list_query(q=q, filters=filters, sort=sort, request=request)
        async with admin.session() as s:
            total = await ma.count(s, stmt)
            rows = (await s.scalars(stmt.limit(per_page).offset((page - 1) * per_page))).all()
        return Page(items=[Read(**read_dict(ma, o, key_of)) for o in rows], total=total, page=page, per_page=per_page)

    @router.get(path + "/{pk}", response_model=Read, tags=[tag], summary=f"Get one {ma.name.lower()}")
    async def get_row(request: Request, pk: str, user: str = User):
        async with admin.session() as s:
            return Read(**read_dict(ma, await fetch(s, request, pk, "view"), key_of))

    @router.post(path, response_model=Read, status_code=201, tags=[tag], dependencies=write_deps, summary=f"Create a {ma.name.lower()}")
    async def create_row(request: Request, body: Create, user: str = User):
        need(user, "add")
        async with admin.session() as s:
            return await write(s, request, None, body, True)

    @router.patch(path + "/{pk}", response_model=Read, tags=[tag], dependencies=write_deps, summary=f"Change a {ma.name.lower()}")
    async def update_row(request: Request, pk: str, body: Update, user: str = User):
        async with admin.session() as s:
            return await write(s, request, await fetch(s, request, pk, "change"), body, False)

    @router.delete(path + "/{pk}", status_code=204, tags=[tag], dependencies=write_deps, summary=f"Delete a {ma.name.lower()}")
    async def delete_row(request: Request, pk: str, user: str = User):
        async with admin.session() as s:
            obj = await fetch(s, request, pk, "delete")
            stale = admin._files(ma, obj)
            label = ma.label(obj)
            await call_hook(ma.delete, s, obj)
            audit.record(s, user=user, action="delete", model=ma.slug, obj_id=pk, obj_repr=label)
            try:
                await s.commit()
            except IntegrityError as e:
                await s.rollback()
                raise HTTPException(409, db_message(e)) from None
        for key in stale:
            await admin.storage.delete(key)
        await call_hook(ma.after_delete, obj)
        return Response(status_code=204)
