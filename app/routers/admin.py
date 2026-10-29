import asyncio
import json
import re
import secrets
import time
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import models
from ..db import get_db
from ..config import settings as cfg
from ..payments import PK_MOBILE
from ..pricing import get_settings
from ..uploads import save_upload
from ..realtime import broker, publish_order
from ..security import get_admin, get_admin_flex, make_token, verify_password
from ..serializers import order_detail as _detail
from ..slip import render_slip

router = APIRouter(prefix="/api/admin", tags=["admin"])
STATUSES = {"PENDING", "COMPLETED", "CANCELLED"}  # new order -> PENDING; admin moves it to COMPLETED once paid
PAY_STATUSES = {"UNPAID", "AWAITING_PAYMENT", "PENDING_VERIFICATION", "PAID", "FAILED", "REFUNDED"}
_attempts: dict[str, list[float]] = defaultdict(list)


class LoginIn(BaseModel):
    email: str = Field(max_length=160)
    password: str = Field(max_length=200)


@router.post("/login")
def login(data: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    _attempts[ip] = [t for t in _attempts[ip] if t > time.time() - 60]
    if len(_attempts[ip]) >= 5:
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    _attempts[ip].append(time.time())
    a = db.scalar(select(models.AdminUser).where(models.AdminUser.email == data.email.strip().lower()))
    if a is None or not a.is_active or not verify_password(data.password, a.password_hash):
        raise HTTPException(401, "Wrong email or password")
    return {"token": make_token(a.id)}


@router.get("/stats")
def stats(_: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    O, P = models.Order, models.Payment
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    count = lambda *w: db.scalar(select(func.count(O.id)).where(*w)) or 0  # noqa: E731
    money = lambda *w: db.scalar(select(func.coalesce(func.sum(O.total), 0)).where(*w)) or 0  # noqa: E731
    done = db.scalars(select(O).where(O.status == "COMPLETED", O.completed_at >= start - timedelta(days=6))).all()
    daily: dict[str, int] = defaultdict(int)
    for o in done:
        daily[o.completed_at.date().isoformat()] += o.total
    top = db.execute(
        select(models.OrderItem.product_name, func.sum(models.OrderItem.quantity).label("q"))
        .join(O, O.id == models.OrderItem.order_id).where(O.status != "CANCELLED")
        .group_by(models.OrderItem.product_name).order_by(desc("q")).limit(5)
    ).all()
    pay_count = lambda st: db.scalar(select(func.count(P.id)).join(O, O.id == P.order_id).where(P.status == st, O.status != "CANCELLED")) or 0  # noqa: E731
    return {
        "total_orders": count(), "today_orders": count(O.created_at >= start),
        "pending_orders": count(O.status == "PENDING"), "completed_orders": count(O.status == "COMPLETED"),
        "cancelled_orders": count(O.status == "CANCELLED"),
        "total_sales": money(O.status == "COMPLETED"),  # money actually received (completed orders)
        "pending_amount": money(O.status == "PENDING"),  # still to be collected
        "pending_payments": pay_count("PENDING_VERIFICATION"), "awaiting_payments": pay_count("AWAITING_PAYMENT"),
        "bestsellers": [{"name": n, "quantity": int(q)} for n, q in top],
        "daily_sales": [{"date": (start - timedelta(days=d)).date().isoformat(),
                         "total": daily.get((start - timedelta(days=d)).date().isoformat(), 0)} for d in range(6, -1, -1)],
    }


@router.get("/orders")
def orders(status: str | None = None, method: str | None = None, q: str | None = None, updated_since: datetime | None = None,
           limit: int = 200, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    """Always read straight from the database. `updated_since` lets the dashboard catch up after a dropped live connection."""
    stmt = select(models.Order).join(models.Payment).options(selectinload(models.Order.items), selectinload(models.Order.payment))
    if status: stmt = stmt.where(models.Order.status == status)
    if method: stmt = stmt.where(models.Payment.method == method)
    if updated_since:
        stmt = stmt.where(models.Order.updated_at >= updated_since.replace(tzinfo=updated_since.tzinfo or timezone.utc))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(models.Order.order_number.ilike(like), models.Order.customer_phone.ilike(like), models.Order.customer_name.ilike(like)))
    return [_detail(o) for o in db.scalars(stmt.order_by(desc(models.Order.created_at)).limit(max(1, min(limit, 500))))]


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.get("/orders/stream")
async def orders_stream(request: Request, _: models.AdminUser = Depends(get_admin_flex), db: Session = Depends(get_db)):
    """Live feed (Server-Sent Events): `order.created` and `order.updated`, each carrying the full saved order.
    Browsers can't set headers on EventSource, so the admin token may be passed as ?token=..."""
    db.close()  # authenticated; don't hold a database connection for the lifetime of the stream

    async def gen():
        q = broker.subscribe()
        try:
            yield "retry: 3000\n\n"
            yield _sse("ready", {"ok": True})
            while not await request.is_disconnected():
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=15)
                    yield _sse(event, data)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keeps proxies from closing an idle connection
        finally:
            broker.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive",
        "Content-Encoding": "identity"})


def _order(db: Session, n: str) -> models.Order:
    o = db.scalar(select(models.Order).where(models.Order.order_number == n))
    if o is None:
        raise HTTPException(404, "Order not found")
    return o


@router.get("/orders/{n}")
def order_detail(n: str, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    return _detail(_order(db, n))


@router.get("/orders/{n}/slip", response_class=HTMLResponse)
def order_slip(n: str, autoprint: bool = False, _: models.AdminUser = Depends(get_admin_flex), db: Session = Depends(get_db)):
    """Printable slip. Open /api/admin/orders/{n}/slip?autoprint=1&token=... in a new tab to print straight away."""
    o = _order(db, n)
    s = get_settings(db)
    return HTMLResponse(render_slip(_detail(o), s.business_name, s.phone, autoprint))


class StatusIn(BaseModel):
    status: str


@router.patch("/orders/{n}/status")
def set_status(n: str, data: StatusIn, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    """PENDING -> COMPLETED (payment received) or CANCELLED. Both are final.
    Completing a COD order records the cash as PAID in the same transaction. Returns the saved order."""
    if data.status not in STATUSES:
        raise HTTPException(422, "Invalid status")
    o = _order(db, n)
    if data.status == o.status:
        return _detail(o)
    if o.status != "PENDING":
        raise HTTPException(409, f"This order is already {o.status.lower()} and cannot be changed")
    p = o.payment
    if data.status == "COMPLETED":
        if p.method == "EASYPAISA" and p.status != "PAID":
            raise HTTPException(409, "Verify the Easypaisa payment (mark it PAID) before completing this order")
        if p.method == "COD" and p.status != "PAID":
            p.status = "PAID"  # the cash has come in
        o.completed_at = models.now()
    elif data.status == "CANCELLED" and o.coupon_code:  # give the coupon use back
        c = db.scalar(select(models.Coupon).where(models.Coupon.code == o.coupon_code))
        if c and c.used_count > 0:
            c.used_count -= 1
    elif data.status == "PENDING":
        raise HTTPException(409, "An order cannot go back to pending")
    o.status = data.status
    o.updated_at = models.now()
    db.commit()
    publish_order("order.updated", o)
    return _detail(o)


class PaymentIn(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=300)


@router.patch("/orders/{n}/payment")
def set_payment(n: str, data: PaymentIn, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    if data.status not in PAY_STATUSES:
        raise HTTPException(422, "Invalid payment status")
    o = _order(db, n)
    p = o.payment
    if p.method == "COD" and data.status in ("AWAITING_PAYMENT", "PENDING_VERIFICATION"):
        raise HTTPException(422, "Not valid for a Cash on Delivery order")
    if data.status == "PAID" and o.status == "CANCELLED":
        raise HTTPException(409, "This order is cancelled")
    if data.status == "REFUNDED" and p.status != "PAID":
        raise HTTPException(422, "Only a paid order can be refunded")
    p.status = data.status
    if data.note is not None:
        p.admin_note = data.note
    o.updated_at = models.now()
    db.commit()
    publish_order("order.updated", o)
    return _detail(o)


class SettingsIn(BaseModel):
    business_name: str | None = None
    phone: str | None = None
    announcement: str | None = None
    min_order_value: int | None = Field(default=None, ge=0)
    shop_latitude: float | None = Field(default=None, ge=-90, le=90)  # delivery distance is measured from here
    shop_longitude: float | None = Field(default=None, ge=-180, le=180)
    tax_percent: int | None = Field(default=None, ge=0, le=100)
    cod_enabled: bool | None = None
    easypaisa_enabled: bool | None = None
    easypaisa_name: str | None = None
    easypaisa_number: str | None = None

    @field_validator("easypaisa_number")
    @classmethod
    def _valid_number(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.replace(" ", "").replace("-", "")
        if not PK_MOBILE.match(v):
            raise ValueError("Enter a valid Easypaisa mobile number, e.g. 03143404938")
        return v


def _settings_dict(s: models.BusinessSettings) -> dict:
    return {k: getattr(s, k) for k in SettingsIn.model_fields}


@router.get("/settings")
def get_admin_settings(_: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    s = get_settings(db); db.commit()
    return _settings_dict(s)


@router.put("/settings")
def put_admin_settings(data: SettingsIn, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    s = get_settings(db)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("easypaisa_number", "x") is None:
        raise HTTPException(422, "Easypaisa number cannot be empty")
    for k, v in changes.items():
        setattr(s, k, v)
    if not s.cod_enabled and not s.easypaisa_enabled:
        db.rollback()
        raise HTTPException(422, "At least one payment method must stay enabled")
    if (s.shop_latitude is None) != (s.shop_longitude is None):
        db.rollback()
        raise HTTPException(422, "Set both shop latitude and longitude")
    db.commit()
    return _settings_dict(s)


class ProductPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    base_price: int | None = Field(default=None, ge=0)
    is_available: bool | None = None
    is_bestseller: bool | None = None
    is_featured: bool | None = None


@router.get("/products")
def list_products(_: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    return [{"id": p.id, "name": p.name, "slug": p.slug, "base_price": p.base_price, "is_available": p.is_available,
             "is_bestseller": p.is_bestseller, "is_featured": p.is_featured, "is_demo": p.is_demo, "category": p.category.name, "image_url": p.image_url}
            for p in db.scalars(select(models.Product).options(selectinload(models.Product.category)).order_by(models.Product.category_id, models.Product.sort_order))]


@router.patch("/products/{pid}")
def patch_product(pid: int, data: ProductPatch, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    p = db.get(models.Product, pid)
    if p is None:
        raise HTTPException(404, "Product not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    if "base_price" in data.model_fields_set or "name" in data.model_fields_set:
        p.is_demo = False if p.is_demo and ("name" in data.model_fields_set) else p.is_demo
    db.commit()
    return {"id": p.id, "ok": True}


@router.get("/orders/{n}/receipt")
def get_receipt(n: str, _: models.AdminUser = Depends(get_admin_flex), db: Session = Depends(get_db)):
    o = _order(db, n)
    if not o.payment.receipt_path:
        raise HTTPException(404, "No receipt uploaded")
    return FileResponse(Path(cfg.upload_dir) / "receipts" / o.payment.receipt_path)


@router.get("/categories")
def categories(_: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name} for c in db.scalars(select(models.Category).order_by(models.Category.sort_order))]


class ProductNew(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category_id: int
    base_price: int = Field(ge=0)


@router.post("/products", status_code=201)
def create_product(data: ProductNew, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    if db.get(models.Category, data.category_id) is None:
        raise HTTPException(422, "Unknown category")
    slug = re.sub(r"[^a-z0-9]+", "-", data.name.lower()).strip("-") or "item"
    if db.scalar(select(models.Product.id).where(models.Product.slug == slug)):
        slug = f"{slug}-{secrets.token_hex(2)}"
    p = models.Product(name=data.name.strip(), slug=slug, category_id=data.category_id, base_price=data.base_price, is_demo=False)
    db.add(p); db.commit()
    return {"id": p.id}


@router.delete("/products/{pid}")
def delete_product(pid: int, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    p = db.get(models.Product, pid)
    if p is None:
        raise HTTPException(404, "Product not found")
    db.delete(p); db.commit()  # past orders keep their name and price snapshots
    return {"ok": True}


@router.post("/products/{pid}/image")
async def product_image(pid: int, file: UploadFile = File(...), _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    p = db.get(models.Product, pid)
    if p is None:
        raise HTTPException(404, "Product not found")
    p.image_url = f"/media/products/{await save_upload(file, 'products', 3)}"
    db.commit()
    return {"image_url": p.image_url}


# ---- sizes (variants) and toppings (add-ons) ----
class OptionIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=60)
    price: int = Field(ge=0)
    is_available: bool = True


class OptionsIn(BaseModel):
    variants: list[OptionIn] = Field(max_length=12)
    addons: list[OptionIn] = Field(max_length=20)


def _opts(rows) -> list[dict]:
    return [{"id": r.id, "name": r.name, "price": r.price, "is_available": r.is_available} for r in rows]


@router.get("/products/{pid}/options")
def get_options(pid: int, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    p = db.scalar(select(models.Product).where(models.Product.id == pid).options(selectinload(models.Product.variants), selectinload(models.Product.addons)))
    if p is None:
        raise HTTPException(404, "Product not found")
    return {"variants": _opts(p.variants), "addons": _opts(p.addons)}


def _sync(db: Session, rows: list, incoming: list[OptionIn], model, product_id: int) -> None:
    """Update rows by id (so customers' saved carts keep working), add new ones, delete the removed ones."""
    by_id = {r.id: r for r in rows}
    keep = set()
    for n, o in enumerate(incoming):
        if o.id is not None and o.id in by_id:
            r = by_id[o.id]; r.name, r.price, r.is_available, r.sort_order = o.name.strip(), o.price, o.is_available, n; keep.add(o.id)
        else:
            db.add(model(product_id=product_id, name=o.name.strip(), price=o.price, is_available=o.is_available, sort_order=n))
    for r in rows:
        if r.id not in keep:
            db.delete(r)


@router.put("/products/{pid}/options")
def put_options(pid: int, data: OptionsIn, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    p = db.scalar(select(models.Product).where(models.Product.id == pid).options(selectinload(models.Product.variants), selectinload(models.Product.addons)))
    if p is None:
        raise HTTPException(404, "Product not found")
    _sync(db, list(p.variants), data.variants, models.Variant, p.id)
    _sync(db, list(p.addons), data.addons, models.Addon, p.id)
    db.commit(); db.refresh(p)
    return {"variants": _opts(p.variants), "addons": _opts(p.addons)}
