"""Demo shop admin.

    uvicorn examples.shop.app:app --reload
    open http://127.0.0.1:8000/admin   (admin / admin)
"""

import enum
import os
import random
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, func, select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from fastyfold import Admin, ModelAdmin, action
from fastyfold.security import hash_password, verify_password


class Base(DeclarativeBase):
    pass


class Status(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    shipped = "shipped"
    refunded = "refunded"


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    city: Mapped[str] = mapped_column(String(80), default="Dhaka")
    vip: Mapped[bool] = mapped_column(Boolean, default=False)
    joined: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __str__(self):
        return self.name


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    sku: Mapped[str] = mapped_column(String(40), unique=True)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    stock: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __str__(self):
        return self.name


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    customer: Mapped[Customer] = relationship()
    status: Mapped[Status] = mapped_column(Enum(Status), default=Status.pending)
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    items: Mapped[int] = mapped_column(Integer, default=1)
    carrier: Mapped[str] = mapped_column(String(40), default="Pathao Courier")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __str__(self):
        return f"Order #{self.id}"


class Staff(Base):
    __tablename__ = "staff"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password: Mapped[str] = mapped_column(String(200), default="!")  # "!" never verifies; set a real hash outside the admin
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)

    def __str__(self):
        return self.username


# ── admin ─────────────────────────────────────────────────────────────────
engine = create_async_engine(os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///shop.db"))


async def authenticate(username: str, password: str) -> str | None:
    async with engine.connect() as conn:
        stored = await conn.scalar(select(Staff.password).where(Staff.username == username))
    return username if stored and verify_password(password, stored) else None


admin = Admin(
    engine,
    secret_key=os.environ.get("SECRET_KEY", "dev-only-change-me"),
    authenticate=authenticate,
    title="Acme Store",
    groups={"Shop": "shopping_bag", "Access": "admin_panel_settings"},
    site_url="/",
)


class OrderIn(BaseModel):
    total: Decimal
    items: int

    @field_validator("total")
    @classmethod
    def positive(cls, v):
        if v is not None and v < 0:
            raise ValueError("Total can't be negative")
        return v


@admin.register
class OrderAdmin(ModelAdmin):
    model = Order
    group = "Shop"
    icon = "receipt_long"
    key = "o"
    list_display = ["id", "customer", "status", "items", "total", "created_at"]
    search_fields = ["customer.name", "customer.email", "note"]
    list_filter = ["status", "created_at"]
    board_field = "status"
    colors = {"pending": "#ff6a2b", "paid": "#10b981", "shipped": "#3b82f6", "refunded": "#a8a29e"}
    fieldsets = [("Order", ["customer_id", "items", "total"]), ("Shipping", ["carrier", "note"])]
    schema = OrderIn

    @action("Mark shipped", icon="local_shipping", key="s")
    async def mark_shipped(self, session, orders):
        for o in orders:
            o.status = Status.shipped

    @action("Refund", icon="undo", confirm="Refund the selected orders?")
    async def refund(self, session, orders):
        for o in orders:
            o.status = Status.refunded


@admin.register
class CustomerAdmin(ModelAdmin):
    model = Customer
    group = "Shop"
    icon = "group"
    key = "c"
    list_display = ["id", "name", "email", "city", "vip", "joined"]


@admin.register
class ProductAdmin(ModelAdmin):
    model = Product
    group = "Shop"
    icon = "inventory_2"
    key = "p"
    list_display = ["id", "name", "sku", "price", "stock", "active"]
    search_fields = ["name", "sku"]


@admin.register
class StaffAdmin(ModelAdmin):
    model = Staff
    group = "Access"
    icon = "badge"
    name_plural = "Staff"
    list_display = ["id", "username", "is_superuser"]
    form_fields = ["username", "is_superuser"]

    def has_permission(self, user, perm):
        return user == "admin"   # only the owner manages staff


# ── seed data ─────────────────────────────────────────────────────────────
NAMES = ["Nusrat Jahan", "Arif Hossain", "Emily Carter", "Tanvir Ahmed", "Sofia Rossi", "Rahim Uddin", "Lena Müller",
         "Farhana Akter", "James Wilson", "Mitu Das", "Kamal Hossain", "Ayesha Rahman", "Sabbir Hasan", "Priya Sen"]
CITIES = ["Dhaka", "Chattogram", "Sylhet", "Khulna", "Berlin", "London", "Rome"]
PRODUCTS = [("Jamdani scarf", 64), ("Clay tea set", 58), ("Nakshi cushion cover", 21), ("Rickshaw art print", 35),
            ("Jute tote bag", 18), ("Brass lamp", 92), ("Hand-woven gamchha", 12), ("Terracotta vase", 44)]


async def seed() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with admin.sessionmaker() as s:
        if await s.scalar(select(func.count()).select_from(Staff)):
            return
        rng = random.Random(42)
        s.add_all([Staff(username="admin", password=hash_password("admin"), is_superuser=True),
                   Staff(username="farhana", password=hash_password("farhana"))])
        customers = [Customer(name=n, email=f"{n.split()[0].lower()}@example.com", city=rng.choice(CITIES), vip=rng.random() < 0.2,
                              joined=datetime.now() - timedelta(days=rng.randint(1, 600))) for n in NAMES]
        s.add_all(customers)
        s.add_all([Product(name=n, sku=f"SKU-{1000 + i * 37}", price=Decimal(p), stock=rng.randint(0, 120), active=rng.random() > 0.1)
                   for i, (n, p) in enumerate(PRODUCTS)])
        weights = [Status.pending] * 2 + [Status.paid] * 6 + [Status.shipped] * 5 + [Status.refunded]
        for i in range(48):
            s.add(Order(customer=rng.choice(customers), status=rng.choice(weights), items=rng.randint(1, 6),
                        total=Decimal(rng.randint(1500, 60000)) / 100, carrier=rng.choice(["Pathao Courier", "RedX", "DHL Express"]),
                        created_at=datetime.now() - timedelta(hours=i * 7 + rng.randint(0, 6))))
        await s.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await seed()
    yield


app = FastAPI(lifespan=lifespan)
admin.mount(app, "/admin")


@app.get("/", include_in_schema=False)
async def home():
    return RedirectResponse("/admin")
