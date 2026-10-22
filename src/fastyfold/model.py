"""ModelAdmin: per-model options and the queries behind list, board and search."""

import enum
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

from sqlalchemy import Select, func, inspect, or_, select
from sqlalchemy.orm import selectinload

from .fields import Field, fields_for, humanize

if TYPE_CHECKING:
    from .admin import Admin

PALETTE = ["#f59e0b", "#10b981", "#3b82f6", "#a8a29e", "#ef4444", "#06b6d4", "#ec4899", "#84cc16"]


def action(label: str, *, icon: str = "bolt", key: str | None = None, confirm: str | None = None) -> Callable:
    """Mark a ModelAdmin method as a bulk action: `async def fn(self, session, objs)`."""
    def wrap(fn):
        fn._ff_action = {"label": label, "icon": icon, "key": key, "confirm": confirm}
        return fn
    return wrap


class ModelAdmin:
    model: type
    name: str | None = None            # singular label, default from the class name
    name_plural: str | None = None
    slug: str | None = None
    group: str = "Models"
    icon: str = "table_rows"
    key: str | None = None             # `G` then this key jumps here

    list_display: list[str] | None = None
    search_fields: list[str] | None = None
    list_filter: list[str] | None = None
    form_fields: list[str] | None = None
    readonly_fields: list[str] = []
    preview_fields: list[str] | None = None
    fieldsets: list[tuple[str, list[str]]] | None = None   # [("Shipping", ["address", "city"]), ...]
    board_field: str | None = None
    choices: dict[str, list] = {}
    colors: dict[str, str] = {}        # choice value -> hex colour
    schema: Any = None                 # optional Pydantic model used to validate forms
    ordering: str | None = None        # e.g. "-created_at"
    per_page: int = 25

    def __init__(self, admin: "Admin"):
        self.admin = admin
        self.mapper = inspect(self.model)
        self.pk = self.mapper.primary_key[0].key
        self.fields: dict[str, Field] = fields_for(self.mapper, self.choices, set(self.readonly_fields))
        cls_name = self.model.__name__
        self.name = self.name or humanize(_snake(cls_name)).capitalize()
        self.name_plural = self.name_plural or _plural(self.name)
        self.slug = self.slug or cls_name.lower()
        cols = list(self.fields)
        self.list_display = self.list_display or cols[:6]
        self.search_fields = self.search_fields if self.search_fields is not None else [n for n, f in self.fields.items() if f.kind == "str"]
        self.list_filter = self.list_filter if self.list_filter is not None else [n for n, f in self.fields.items() if f.kind in ("choice", "bool", "date", "datetime")]
        self.form_fields = self.form_fields or [n for n, f in self.fields.items() if not f.readonly and not f.auto]
        self.preview_fields = self.preview_fields or self.list_display
        self.relations = [r.key for r in self.mapper.relationships if r.direction.name == "MANYTOONE"]
        if not self.fieldsets:
            main = [n for n in self.form_fields if n != self.board_field]
            self.fieldsets = [("Details", main)] if main else []
        self.actions = {
            n: {**fn._ff_action, "name": n}
            for n in dir(type(self)) if not n.startswith("_") and hasattr(fn := getattr(type(self), n), "_ff_action")
        }
        # The first plain-text column in the list doubles as the row title.
        self.title_field = next((n for n in self.list_display if n in self.fields and self.fields[n].kind == "str"), None)
        self.subtitle_field = next((n for n in self.list_display if n != self.title_field and n in self.fields and self.fields[n].kind == "str"), None)
        # Records without a text title (e.g. orders) borrow a face from their first relation.
        self.lead_fk = next((n for n in self.list_display if n in self.relations or (n in self.fields and self.fields[n].kind == "fk")), None)

    # ── permissions and hooks ─────────────────────────────────────────────
    def has_permission(self, user: str, perm: str) -> bool:
        """perm: view, add, change, delete, or an action name. Override per model."""
        return True

    async def save(self, session, obj, is_new: bool) -> None:
        session.add(obj)

    async def delete(self, session, obj) -> None:
        await session.delete(obj)

    # ── values ────────────────────────────────────────────────────────────
    def pk_of(self, obj):
        return getattr(obj, self.pk)

    def label(self, obj) -> str:
        text = str(obj)
        return f"{self.name} #{self.pk_of(obj)}" if text.startswith("<") else text

    def value(self, obj, name: str):
        for part in name.split("."):
            obj = getattr(obj, part, None) if obj is not None else None
        return obj

    def avatar_text(self, obj) -> str:
        if self.title_field:
            return self.display(obj, self.title_field)
        return self.display(obj, self.lead_fk) if self.lead_fk else self.label(obj)

    def subtitle(self, obj) -> str:
        if self.subtitle_field:
            return self.display(obj, self.subtitle_field)
        return self.display(obj, self.lead_fk) if self.lead_fk and not self.title_field else ""

    def card_fields(self) -> list[str]:
        """Extra list columns for board cards, numbers first."""
        used = {self.pk, self.board_field, self.title_field, self.subtitle_field, self.lead_fk}
        rest = [n for n in self.list_display if n not in used]
        return sorted(rest, key=lambda n: 0 if self.fields.get(n) and self.fields[n].kind == "decimal" else 1)

    def column_label(self, name: str) -> str:
        if name == self.pk:
            return "ID"
        if name in self.fields:
            return self.fields[name].label
        return humanize(name.replace(".", " "))

    def display(self, obj, name: str) -> str:
        f = self.fields.get(name)
        val = self.value(obj, name)
        if f and f.kind == "fk" and f.relation:
            rel = getattr(obj, f.relation, None)
            return str(rel) if rel is not None else "—"
        return f.display(val) if f else ("—" if val is None else str(val))

    def raw(self, obj, name: str) -> str:
        val = self.value(obj, name)
        return str(val.value if isinstance(val, enum.Enum) else val)

    def color(self, name: str, value) -> str:
        value = str(value.value if isinstance(value, enum.Enum) else value)
        if value in self.colors:
            return self.colors[value]
        options = [v for v, _ in self.fields[name].choices] if name in self.fields else []
        return PALETTE[options.index(value) % len(PALETTE)] if value in options else PALETTE[3]

    def is_badge(self, name: str) -> bool:
        f = self.fields.get(name)
        return bool(f and f.kind == "choice")

    # ── queries ───────────────────────────────────────────────────────────
    def base_query(self) -> Select:
        q = select(self.model)
        for rel in self.relations:
            q = q.options(selectinload(getattr(self.model, rel)))
        return q

    def _column(self, name: str, joined: set):
        """Column for `name` or `relation.column`; records which relations need a join."""
        if "." not in name:
            return getattr(self.model, name)
        rel, col = name.split(".", 1)
        target = self.mapper.relationships[rel].mapper.class_
        joined.add(rel)
        return getattr(target, col)

    def list_query(self, *, q: str = "", filters: dict[str, str] | None = None, sort: str | None = None) -> Select:
        stmt = self.base_query()
        joined: set[str] = set()
        if q and self.search_fields:
            stmt = stmt.where(or_(*[self._column(n, joined).icontains(q, autoescape=True) for n in self.search_fields]))
        for name, raw in (filters or {}).items():
            cond = self.filter_condition(name, raw)
            if cond is not None:
                stmt = stmt.where(cond)
        for rel in joined:
            stmt = stmt.outerjoin(getattr(self.model, rel))
        sort = sort or self.ordering or f"-{self.pk}"
        name = sort.lstrip("-")
        if name in self.fields:
            col = getattr(self.model, name)
            stmt = stmt.order_by(col.desc() if sort.startswith("-") else col.asc())
        return stmt

    def filter_condition(self, name: str, raw: str):
        f = self.fields.get(name)
        if not f or raw in ("", None) or name not in self.list_filter:
            return None
        col = getattr(self.model, name)
        if f.kind == "bool":
            return col.is_(raw == "1")
        if f.kind == "choice":
            try:
                return col == f.parse(raw)
            except ValueError:
                return None
        if f.kind in ("date", "datetime"):
            days = {"today": 0, "7d": 7, "30d": 30}.get(raw)
            if days is None:
                return None
            aware = getattr(col.type, "timezone", False)
            now = datetime.now(timezone.utc) if aware else datetime.now()
            start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
            return col >= (start if f.kind == "datetime" else start.date())
        return None

    def filter_options(self) -> list[dict]:
        out = []
        for name in self.list_filter:
            f = self.fields.get(name)
            if not f:
                continue
            if f.kind == "choice":
                opts = f.choices
            elif f.kind == "bool":
                opts = [("1", "Yes"), ("0", "No")]
            elif f.kind in ("date", "datetime"):
                opts = [("today", "Today"), ("7d", "Last 7 days"), ("30d", "Last 30 days")]
            else:
                continue
            out.append({"name": name, "label": f.label, "options": opts, "kind": f.kind})
        return out

    async def count(self, session, stmt: Select | None = None) -> int:
        stmt = stmt if stmt is not None else select(self.model)
        return await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0

    async def board_counts(self, session) -> dict[str, int]:
        if not self.board_field:
            return {}
        col = getattr(self.model, self.board_field)
        rows = await session.execute(select(col, func.count()).group_by(col))
        return {str(v.value if isinstance(v, enum.Enum) else v): n for v, n in rows}

    async def fk_options(self, session, f: Field) -> list[tuple[str, str]]:
        # ponytail: loads up to 200 related rows into a <select>; add an autocomplete endpoint for big tables.
        target_pk = inspect(f.target).primary_key[0].key
        rows = (await session.scalars(select(f.target).order_by(getattr(f.target, target_pk)).limit(200))).all()
        return [(str(getattr(r, target_pk)), str(r)) for r in rows]


def _snake(name: str) -> str:
    return "".join(f"_{c.lower()}" if c.isupper() and i else c.lower() for i, c in enumerate(name))


def _plural(word: str) -> str:
    if word.endswith("y") and word[-2:-1] not in "aeiou":
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    return word + "s"
