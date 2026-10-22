"""Models the suite runs against. Between them they cover every column kind fastyfold maps."""

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Status(str, enum.Enum):
    draft = "draft"
    review = "review"
    published = "published"
    archived = "archived"


class Author(Base):
    __tablename__ = "authors"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    born: Mapped[date | None] = mapped_column(Date, nullable=True)
    joined: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __str__(self):
        return self.name


class Post(Base):
    __tablename__ = "posts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id"))
    author: Mapped[Author] = relationship()
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.draft)
    views: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)

    def __str__(self):
        return self.title


class Tag(Base):
    """No __str__, a readonly column and a `choices` override."""
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="topic")


class Secret(Base):
    """Only the `admin` user may see this one."""
    __tablename__ = "secrets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(String(50))

    def __str__(self):
        return self.value
