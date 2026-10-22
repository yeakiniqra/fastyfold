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


async def recent(session: AsyncSession, *, model: str | None = None, object_id=None, limit: int = 10, offset: int = 0) -> list[LogEntry]:
    q = select(LogEntry).order_by(LogEntry.id.desc()).limit(limit).offset(offset)
    if model:
        q = q.where(LogEntry.model == model)
    if object_id is not None:
        q = q.where(LogEntry.object_id == str(object_id))
    return list((await session.scalars(q)).all())
