"""Models the suite runs against. Between them they cover every column kind fastyfold maps."""

import enum
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import JSON, Boolean, Column, Date, DateTime, Enum, ForeignKey, Integer, Interval, LargeBinary, Numeric, String, Table, Text, Time, Uuid, func
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
    email: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    born: Mapped[date | None] = mapped_column(Date, nullable=True)
    joined: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    photo: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cv: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __str__(self):
        return self.name


post_tags = Table(
    "post_tags", Base.metadata,
    Column("post_id", ForeignKey("posts.id"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id"), primary_key=True),
)


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
    tags: Mapped[list["Tag"]] = relationship(secondary=post_tags)
    comments: Mapped[list["Comment"]] = relationship(back_populates="post", cascade="all, delete-orphan")

    def __str__(self):
        return self.title


class Comment(Base):
    """Edited inline on its post."""
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"))
    post: Mapped[Post] = relationship(back_populates="comments")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("authors.id"), nullable=True)
    author: Mapped[Author | None] = relationship()
    body: Mapped[str] = mapped_column(String(200))
    likes: Mapped[int] = mapped_column(Integer, default=0)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)


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


class Ticket(Base):
    """A UUID primary key and column types fastyfold only displays (Interval, LargeBinary)."""
    __tablename__ = "tickets"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(20))
    opens: Mapped[time | None] = mapped_column(Time, nullable=True)
    window: Mapped[timedelta | None] = mapped_column(Interval, nullable=True)
    blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    def __str__(self):
        return self.code


class Seat(Base):
    """A foreign key to a UUID key."""
    __tablename__ = "seats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(20))
    ticket_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tickets.id"))
    ticket: Mapped[Ticket] = relationship()
