"""All money is whole PKR (int). Order items are snapshots."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now() -> datetime:
    return datetime.now(timezone.utc)


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    products: Mapped[list["Product"]] = relationship(back_populates="category", order_by="Product.sort_order")


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(String(500))
    base_price: Mapped[int] = mapped_column(Integer)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    is_bestseller: Mapped[bool] = mapped_column(Boolean, default=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False)
    prep_minutes: Mapped[int | None] = mapped_column(Integer)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    category: Mapped[Category] = relationship(back_populates="products")
    variants: Mapped[list["Variant"]] = relationship(cascade="all, delete-orphan", order_by="Variant.sort_order")
    addons: Mapped[list["Addon"]] = relationship(cascade="all, delete-orphan", order_by="Addon.sort_order")
    __table_args__ = (Index("ix_products_cat", "category_id", "sort_order"),)


class Variant(Base):
    __tablename__ = "variants"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    price: Mapped[int] = mapped_column(Integer)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Addon(Base):
    __tablename__ = "addons"
    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    price: Mapped[int] = mapped_column(Integer)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class Coupon(Base):
    __tablename__ = "coupons"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    type: Mapped[str] = mapped_column(String(10))  # PERCENT | FIXED
    value: Mapped[int] = mapped_column(Integer)
    max_discount: Mapped[int | None] = mapped_column(Integer)
    min_subtotal: Mapped[int | None] = mapped_column(Integer)
    usage_limit: Mapped[int | None] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class BusinessSettings(Base):
    """Single row (id=1). Editable from the admin dashboard in a later phase."""
    __tablename__ = "business_settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    business_name: Mapped[str] = mapped_column(String(80), default="Cravy")
    phone: Mapped[str | None] = mapped_column(String(30))
    announcement: Mapped[str | None] = mapped_column(String(200))
    delivery_charge: Mapped[int] = mapped_column(Integer, default=0)
    min_order_value: Mapped[int] = mapped_column(Integer, default=0)
    tax_percent: Mapped[int] = mapped_column(Integer, default=0)  # added on top of (subtotal - discount)
    cod_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    easypaisa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    easypaisa_name: Mapped[str | None] = mapped_column(String(80))
    easypaisa_number: Mapped[str | None] = mapped_column(String(30))
    bank_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    bank_name: Mapped[str | None] = mapped_column(String(80))
    bank_account_title: Mapped[str | None] = mapped_column(String(80))
    bank_account_number: Mapped[str | None] = mapped_column(String(40))
    bank_iban: Mapped[str | None] = mapped_column(String(40))


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_number: Mapped[str] = mapped_column(String(30), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="PENDING_CONFIRMATION", index=True)
    customer_name: Mapped[str] = mapped_column(String(120))
    customer_phone: Mapped[str] = mapped_column(String(30), index=True)
    customer_email: Mapped[str | None] = mapped_column(String(160))
    city: Mapped[str] = mapped_column(String(80))
    address: Mapped[str] = mapped_column(String(300))
    landmark: Mapped[str | None] = mapped_column(String(160))
    instructions: Mapped[str | None] = mapped_column(String(300))
    subtotal: Mapped[int] = mapped_column(Integer)
    delivery_charge: Mapped[int] = mapped_column(Integer, default=0)
    discount_total: Mapped[int] = mapped_column(Integer, default=0)
    tax_total: Mapped[int] = mapped_column(Integer, default=0)
    delivery_zone: Mapped[str | None] = mapped_column(String(80))  # snapshot of the zone name
    total: Mapped[int] = mapped_column(Integer)
    coupon_code: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    payment: Mapped["Payment"] = relationship(back_populates="order", uselist=False, cascade="all, delete-orphan")


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(Integer)
    product_name: Mapped[str] = mapped_column(String(120))
    variant_name: Mapped[str | None] = mapped_column(String(60))
    addons: Mapped[list] = mapped_column(JSON, default=list)
    unit_price: Mapped[int] = mapped_column(Integer)
    quantity: Mapped[int] = mapped_column(Integer)
    line_total: Mapped[int] = mapped_column(Integer)
    order: Mapped[Order] = relationship(back_populates="items")


class Payment(Base):
    __tablename__ = "payments"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    method: Mapped[str] = mapped_column(String(20))  # COD | EASYPAISA | BANK_TRANSFER | GATEWAY
    status: Mapped[str] = mapped_column(String(25), default="UNPAID")
    amount: Mapped[int] = mapped_column(Integer)
    transaction_ref: Mapped[str | None] = mapped_column(String(120))
    receipt_path: Mapped[str | None] = mapped_column(String(300))
    admin_note: Mapped[str | None] = mapped_column(String(300))
    order: Mapped[Order] = relationship(back_populates="payment")
    __table_args__ = (Index("ix_payment_status", "status", "method"),)


class AdminUser(Base):
    __tablename__ = "admin_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(160), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(10), default="OWNER")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class NotificationLog(Base):
    """A row means we attempted a send. status SENT is set only when the SMTP server accepted the message."""
    __tablename__ = "notification_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id", ondelete="SET NULL"), index=True)
    channel: Mapped[str] = mapped_column(String(12))
    recipient: Mapped[str] = mapped_column(String(160))
    template: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(10), default="QUEUED")
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeliveryZone(Base):
    __tablename__ = "delivery_zones"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    city: Mapped[str] = mapped_column(String(80))
    charge: Mapped[int] = mapped_column(Integer, default=0)
    free_above: Mapped[int | None] = mapped_column(Integer)  # subtotal at or above this ships free
    min_order: Mapped[int | None] = mapped_column(Integer)  # overrides the global minimum
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
