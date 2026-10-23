"""Turn SQLAlchemy columns into form/list fields: widget kind, parsing and display."""

import enum
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, Float, Integer, Interval, Numeric, String, Text, Time, Uuid
from sqlalchemy.orm import ColumnProperty, Mapper


class FieldError(ValueError):
    pass


@dataclass
class Field:
    name: str
    label: str
    kind: str                       # int decimal str text bool date datetime time uuid choice fk json m2m image file other
    required: bool = False
    readonly: bool = False
    choices: list[tuple[str, str]] = field(default_factory=list)   # (value, label)
    enum_cls: type[enum.Enum] | None = None
    default: object = None          # scalar Python default, pre-filled on new forms
    auto: bool = False              # filled by the database (server_default); off forms unless listed
    relation: str | None = None     # relationship attribute name for fk and m2m fields
    target: type | None = None      # related model class for fk and m2m fields
    key_kind: str = "int"           # fk only: kind of the referenced key (int, str or uuid)
    help: str = ""

    def parse(self, raw: str | None):
        """Form string -> Python value. Raises FieldError with a human message."""
        raw = (raw or "").strip() if self.kind != "text" else (raw or "")
        if self.kind == "bool":
            return raw in ("on", "true", "1", "yes")
        if raw == "":
            if self.required:
                raise FieldError("This field is required.")
            return None
        kind = self.key_kind if self.kind == "fk" else self.kind
        try:
            if kind == "int":
                return int(raw)
            if kind == "uuid":
                return uuid.UUID(raw)
            if kind == "decimal":
                return Decimal(raw)
            if self.kind == "date":
                return date.fromisoformat(raw)
            if self.kind == "datetime":
                return datetime.fromisoformat(raw)
            if self.kind == "time":
                return time.fromisoformat(raw)
            if self.kind == "json":
                return json.loads(raw)
        except (ValueError, InvalidOperation):
            raise FieldError(f"Enter a valid {self.kind_label}.") from None
        if self.kind == "choice":
            if raw not in {v for v, _ in self.choices}:
                raise FieldError("Select a valid option.")
            return self.enum_cls(raw) if self.enum_cls else raw
        return raw

    @property
    def kind_label(self) -> str:
        return {"int": "whole number", "fk": "option", "decimal": "number", "date": "date", "time": "time",
                "datetime": "date and time", "json": "JSON value", "uuid": "UUID"}.get(self.kind, "value")

    def form_value(self, value) -> str:
        """Python value -> string for an <input value>."""
        if value is None:
            return ""
        if isinstance(value, enum.Enum):
            return str(value.value)
        if self.kind == "datetime" and isinstance(value, datetime):
            return value.strftime("%Y-%m-%dT%H:%M")
        if self.kind == "time" and isinstance(value, time):
            return value.strftime("%H:%M")
        if self.kind == "json":
            return json.dumps(value, indent=2)
        return str(value)

    def display(self, value) -> str:
        if value is None:
            return "—"
        if isinstance(value, enum.Enum):
            value = value.value
        if self.kind == "choice":
            return dict(self.choices).get(str(value), str(value))
        if self.kind == "bool":
            return "Yes" if value else "No"
        if self.kind == "datetime" and isinstance(value, datetime):
            return value.strftime("%b %d, %H:%M")
        if self.kind == "date" and isinstance(value, date):
            return value.strftime("%b %d, %Y")
        if self.kind == "time" and isinstance(value, time):
            return value.strftime("%H:%M")
        if self.kind == "decimal":
            return f"{value:,.2f}"
        if self.kind in ("image", "file"):
            return str(value).rsplit("/", 1)[-1]
        return str(value)


def humanize(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def _kind(col) -> str:
    return "fk" if col.foreign_keys else _type_kind(col.type)


def _type_kind(t) -> str:
    """Kind for a column type. Types fastyfold can't edit safely (Interval, ARRAY, LargeBinary…) are "other": shown, never edited."""
    if isinstance(t, Interval):
        return "other"
    t = getattr(t, "impl_instance", t)      # a TypeDecorator behaves like the type it wraps
    if isinstance(t, Enum):
        return "choice"
    if isinstance(t, Boolean):
        return "bool"
    if isinstance(t, DateTime):
        return "datetime"
    if isinstance(t, Date):
        return "date"
    if isinstance(t, Time):
        return "time"
    if isinstance(t, Uuid):
        return "uuid"
    if isinstance(t, Integer):
        return "int"
    if isinstance(t, (Numeric, Float)):
        return "decimal"
    if isinstance(t, JSON):
        return "json"
    if isinstance(t, Text) or (isinstance(t, String) and (t.length or 0) > 255):
        return "text"
    return "str" if isinstance(t, String) else "other"


def fields_for(mapper: Mapper, choices: dict[str, list] | None = None, readonly: set[str] = frozenset(),
               files: dict[str, str] | None = None) -> dict[str, Field]:
    """Columns first, then many-to-many relationships. `files` maps a string column to "image" or "file"."""
    choices, files = choices or {}, files or {}
    fk_relations = {}
    for rel in mapper.relationships:
        if rel.direction.name == "MANYTOONE":
            for col in rel.local_columns:
                fk_relations[col.key] = (rel.key, rel.mapper.class_)

    out: dict[str, Field] = {}
    for prop in mapper.column_attrs:
        if not isinstance(prop, ColumnProperty) or len(prop.columns) != 1:
            continue
        col = prop.columns[0]
        kind = _kind(col)
        default = col.default.arg if col.default is not None and col.default.is_scalar else None
        f = Field(
            name=prop.key,
            label=humanize(prop.key.removesuffix("_id") if kind == "fk" else prop.key),
            kind=kind,
            required=not col.nullable and not col.primary_key and kind not in ("bool", "other"),
            readonly=col.primary_key or prop.key in readonly or kind == "other",
            default=default,
            auto=col.server_default is not None,
        )
        if isinstance(col.type, Enum):
            f.enum_cls = col.type.enum_class
            values = [e.value for e in f.enum_cls] if f.enum_cls else list(col.type.enums)
            f.choices = [(str(v), humanize(str(v))) for v in values]
        if prop.key in choices:
            f.kind = "choice"
            f.choices = [(str(c[0]), str(c[1])) if isinstance(c, (tuple, list)) else (str(c), humanize(str(c))) for c in choices[prop.key]]
        if kind == "fk":
            f.key_kind = _type_kind(col.type)
            if prop.key in fk_relations:
                f.relation, f.target = fk_relations[prop.key]
        if prop.key in files:
            f.kind = files[prop.key]
        out[prop.key] = f
    for rel in mapper.relationships:
        if rel.direction.name == "MANYTOMANY":
            out[rel.key] = Field(name=rel.key, label=humanize(rel.key), kind="m2m", relation=rel.key,
                                 target=rel.mapper.class_, readonly=rel.key in readonly)
    return out
