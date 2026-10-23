"""Audit log: who changed what, stored next to the user's own tables."""

import json
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, MetaData, String, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class _Base(DeclarativeBase):
    metadata = MetaData()


class LogEntry(_Base):
    __tablename__ = "fastyfold_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    user: Mapped[str] = mapped_column(String(150))
    action: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(100), index=True)
    object_id: Mapped[str] = mapped_column(String(100))
    object_repr: Mapped[str] = mapped_column(String(200))
    changes: Mapped[str] = mapped_column(Text, default="{}")

    @property
    def changed(self) -> dict:
        return json.loads(self.changes or "{}")


async def ensure_table(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)


def record(session: AsyncSession, *, user: str, action: str, model: str, obj_id, obj_repr: str, changes: dict | None = None) -> None:
    session.add(LogEntry(
        user=user, action=action, model=model, object_id=str(obj_id),
        object_repr=obj_repr[:200], changes=json.dumps(changes or {}, default=str),
    ))


async def recent(session: AsyncSession, *, model: str | None = None, models: list[str] | None = None, object_id=None,
                 user: str | None = None, action: str | None = None, since: datetime | None = None,
                 limit: int = 10, offset: int = 0) -> list[LogEntry]:
    """Newest first. `models` limits entries to models the viewer may see."""
    q = select(LogEntry).order_by(LogEntry.id.desc()).limit(limit).offset(offset)
    if model:
        q = q.where(LogEntry.model == model)
    if models is not None:
        q = q.where(LogEntry.model.in_(models))
    if object_id is not None:
        q = q.where(LogEntry.object_id == str(object_id))
    if user:
        q = q.where(LogEntry.user == user)
    if action:
        q = q.where(LogEntry.action == action)
    if since is not None:
        q = q.where(LogEntry.at >= since)
    return list((await session.scalars(q)).all())


async def distinct(session: AsyncSession, column: str, *, models: list[str]) -> list[str]:
    """Distinct `user` or `action` values, for the log's filter menus."""
    col = getattr(LogEntry, column)
    q = select(col).where(LogEntry.model.in_(models)).distinct().order_by(col).limit(100)
    return list((await session.scalars(q)).all())
