import asyncio
import json
import re
import secrets
import time
from pathlib import Path
from collections import defaultdict
from datetime import date as Date, datetime, timedelta, timezone

from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import models
from ..db import get_db
from ..config import settings as cfg
from ..discounts import apply_discount
from ..payments import PK_MOBILE
from ..pricing import get_settings
from ..uploads import save_upload
from .. import storage
from ..realtime import broker, publish_order
from ..security import get_admin, get_admin_flex, is_super_admin, make_token, verify_password
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
    if a is None or not a.is_active or not verify_password(data.password, a.password_hash) or not is_super_admin(a):
        raise HTTPException(401, "Wrong email or password")  # a non-super admin gets the same answer: nothing is revealed
    return {"token": make_token(a.id), "role": "superadmin"}


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


SHOP_TZ = timezone(timedelta(hours=5))  # Pakistan time (no daylight saving): a day ends at local midnight, not UTC midnight
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _shop_dt(dt: datetime) -> datetime:
    """A stored time as shop-local time (SQLite hands back naive UTC times)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(SHOP_TZ)


def _shop_date(dt: datetime):
    return _shop_dt(dt).date()


def _hour_label(h: int) -> str:
    return f"{h % 12 or 12}{'a' if h < 12 else 'p'}"


@router.get("/sales")
def sales(year: int | None = Query(None, ge=2000, le=2100), month: int | None = Query(None, ge=0, le=12),
          days: int | None = Query(None, ge=1, le=90), day: Date | None = Query(None, alias="date"),
          _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    """Money received (COMPLETED orders, by completion day, shop time) for the dashboard chart.
    ?year=2026&month=11 -> one bar per day | ?year=2026 (or month=0) -> one bar per month | ?days=7 -> last 7 days
    | ?date=2026-11-20 -> one bar per hour plus the list of that day's orders.
    No parameters = the current month. Always read straight from the database, so it is as live as the orders."""
    O = models.Order
    today = datetime.now(SHOP_TZ).date()

    def window(start, end):  # inclusive shop-local dates -> {date: [total, orders]}
        lo = datetime(start.year, start.month, start.day, tzinfo=SHOP_TZ).astimezone(timezone.utc)
        nxt = end + timedelta(days=1)
        hi = datetime(nxt.year, nxt.month, nxt.day, tzinfo=SHOP_TZ).astimezone(timezone.utc)
        rows = db.execute(select(O.completed_at, O.total).where(O.status == "COMPLETED", O.completed_at >= lo, O.completed_at < hi)).all()
        out: dict = defaultdict(lambda: [0, 0])
        for at, total in rows:
            cell = out[_shop_date(at)]
            cell[0] += total
            cell[1] += 1
        return out

    def month_bounds(y, m):
        first = datetime(y, m, 1).date()
        last = (datetime(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).date()
        return first, last

    if day is not None:
        mode, start, end = "day", day, day
        prev_start = prev_end = day - timedelta(days=1)
    elif days:
        mode, start, end = "days", today - timedelta(days=days - 1), today
        prev_start, prev_end = start - timedelta(days=days), start - timedelta(days=1)
    elif year is not None and not month:
        mode, start, end = "year", datetime(year, 1, 1).date(), datetime(year, 12, 31).date()
        prev_start, prev_end = datetime(year - 1, 1, 1).date(), datetime(year - 1, 12, 31).date()
    else:
        y, m = (year, month) if year is not None and month else (today.year, today.month)
        mode, (start, end) = "month", month_bounds(y, m)
        py, pm = (y - 1, 12) if m == 1 else (y, m - 1)
        prev_start, prev_end = month_bounds(py, pm)

    partial = mode != "day" and start <= today < end  # the period is still running: compare it with the same stretch of the previous period, not a whole one
    if partial and mode == "month":
        prev_end = min(prev_end, prev_start + (today - start))
    elif partial and mode == "year":
        try:
            prev_end = today.replace(year=start.year - 1)
        except ValueError:  # 29 February
            prev_end = today.replace(year=start.year - 1, day=28)

    orders_list: list = []
    points = []
    if mode == "day":
        lo = datetime(start.year, start.month, start.day, tzinfo=SHOP_TZ).astimezone(timezone.utc)
        rows = db.scalars(
            select(O).options(selectinload(O.items), selectinload(O.payment))
            .where(O.status == "COMPLETED", O.completed_at >= lo, O.completed_at < lo + timedelta(days=1)).order_by(O.completed_at)
        ).all()
        hours = [[0, 0] for _ in range(24)]
        for o in rows:
            local = _shop_dt(o.completed_at)
            hours[local.hour][0] += o.total
            hours[local.hour][1] += 1
            orders_list.append({
                "order_number": o.order_number, "time": local.strftime("%H:%M"), "total": o.total, "customer": o.customer_name,
                "method": o.payment.method if o.payment else None,
                "items": ", ".join(f"{i.product_name} x{i.quantity}" for i in o.items)[:140],
            })
        now_h = datetime.now(SHOP_TZ).hour
        points = [{"key": f"{start.isoformat()}T{h:02d}", "label": _hour_label(h), "total": hours[h][0], "orders": hours[h][1],
                   "future": start > today or (start == today and h > now_h)} for h in range(24)]
        partial = start == today
    elif mode == "year":
        got = window(start, end)
        for m in range(1, 13):
            tot = sum(v[0] for d, v in got.items() if d.month == m)
            cnt = sum(v[1] for d, v in got.items() if d.month == m)
            points.append({"key": f"{start.year}-{m:02d}", "label": MONTHS[m - 1], "total": tot, "orders": cnt, "future": (start.year, m) > (today.year, today.month)})
    else:
        got = window(start, end)
        d = start
        while d <= end:
            tot, cnt = got.get(d, (0, 0))
            points.append({"key": d.isoformat(), "label": str(d.day), "total": tot, "orders": cnt, "future": d > today})
            d += timedelta(days=1)

    total = sum(p["total"] for p in points)
    orders_n = sum(p["orders"] for p in points)
    prev_total = sum(v[0] for v in window(prev_start, prev_end).values())
    best = max((p for p in points if p["total"] > 0), key=lambda p: p["total"], default=None)
    first = db.scalar(select(func.min(O.completed_at)).where(O.status == "COMPLETED"))
    first_date = _shop_date(first) if first else None
    return {
        "mode": mode, "year": start.year, "month": start.month if mode in ("month", "day") else 0, "days": days,
        "date": start.isoformat() if mode == "day" else None, "orders_list": orders_list,
        "points": points, "total": total, "orders": orders_n,
        "average_order": round(total / orders_n) if orders_n else 0,
        "best": {"key": best["key"], "label": best["label"], "total": best["total"]} if best else None,
        "previous_total": prev_total, "partial": partial,
        "change_percent": round((total - prev_total) * 100 / prev_total, 1) if prev_total else None,
        "first_sale": {"year": first_date.year, "month": first_date.month} if first_date else None,
        "years": list(range(first_date.year, today.year + 1)) if first_date else [today.year],
        "today": today.isoformat(), "current": {"year": today.year, "month": today.month},
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
    discount_percent: int | None = Field(default=None, ge=0, le=90)  # the admin's own discount for this product (0 = none)


@router.get("/products")
def list_products(_: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    return [{"id": p.id, "name": p.name, "slug": p.slug, "base_price": p.base_price, "is_available": p.is_available,
             "is_bestseller": p.is_bestseller, "is_featured": p.is_featured, "is_demo": p.is_demo, "category": p.category.name, "image_url": p.image_url, "images": p.images,
             "discount_percent": p.discount_percent or 0, "final_base_price": apply_discount(p.base_price, p.discount_percent)}
            for p in db.scalars(select(models.Product).options(selectinload(models.Product.category), selectinload(models.Product.image_rows)).order_by(models.Product.category_id, models.Product.sort_order))]


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
    return {"id": p.id, "ok": True, "discount_percent": p.discount_percent or 0, "final_base_price": apply_discount(p.base_price, p.discount_percent)}


@router.get("/orders/{n}/receipt")
def get_receipt(n: str, _: models.AdminUser = Depends(get_admin_flex), db: Session = Depends(get_db)):
    o = _order(db, n)
    if not o.payment.receipt_path:
        raise HTTPException(404, "No receipt uploaded")
    got = storage.get("receipts", o.payment.receipt_path)
    if got is None:
        raise HTTPException(404, "The receipt file is missing")
    return Response(got[0], media_type=got[1])


@router.get("/categories")
def categories(_: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    return [{"id": c.id, "name": c.name} for c in db.scalars(select(models.Category).order_by(models.Category.sort_order))]


class CategoryNew(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=80)


@router.post("/categories", status_code=201)
def create_category(data: CategoryNew, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    """Add a menu category from the admin panel. A name that already exists (any capitalisation) returns that category, never a duplicate."""
    name = re.sub(r"\s+", " ", data.name).strip()
    same = db.scalar(select(models.Category).where(func.lower(models.Category.name) == name.lower()))
    if same is not None:
        return {"id": same.id, "name": same.name, "slug": same.slug, "created": False}
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "category"
    if db.scalar(select(models.Category.id).where(models.Category.slug == slug)):
        slug = f"{slug}-{secrets.token_hex(2)}"
    last = db.scalar(select(func.max(models.Category.sort_order))) or 0
    c = models.Category(name=name, slug=slug, sort_order=last + 1, is_active=True)
    db.add(c); db.commit()
    return {"id": c.id, "name": c.name, "slug": c.slug, "created": True}


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


MAX_IMAGES = 8  # photo slots per product


def _product_for_images(db: Session, pid: int) -> models.Product:
    p = db.scalar(select(models.Product).where(models.Product.id == pid).options(selectinload(models.Product.image_rows)))
    if p is None:
        raise HTTPException(404, "Product not found")
    return p


def _images_out(p: models.Product) -> dict:
    return {"image_url": p.image_url, "images": p.images}


@router.post("/products/{pid}/image")
async def product_image(pid: int, file: UploadFile = File(...), slot: int = Query(0, ge=0, lt=MAX_IMAGES),
                        _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    """Put one WebP photo in a slot. slot=0 is the main photo (the default, as before). A slot that already has a photo
    is replaced; a slot after the last one is added at the end (so the photos always stay in order, with no gaps)."""
    p = _product_for_images(db, pid)
    urls = p.images
    url = f"/media/products/{await save_upload(file, 'products', 3, webp_only=True)}"  # WebP only
    if slot < len(urls):
        urls[slot] = url
    elif len(urls) < MAX_IMAGES:
        urls.append(url)
    p.set_images(urls)
    db.commit()
    return _images_out(p)


@router.post("/products/{pid}/images")
async def product_images_add(pid: int, files: list[UploadFile] = File(...),
                             _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    """Add several WebP photos at once. They fill the next free slots, in the order they were sent."""
    p = _product_for_images(db, pid)
    urls = p.images
    if len(urls) + len(files) > MAX_IMAGES:
        raise HTTPException(422, f"A product can have at most {MAX_IMAGES} photos")
    saved = [f"/media/products/{await save_upload(f, 'products', 3, webp_only=True)}" for f in files]  # nothing is added if one is not WebP
    p.set_images(urls + saved)
    db.commit()
    return _images_out(p)


@router.delete("/products/{pid}/image")
def product_image_delete(pid: int, slot: int = Query(0, ge=0, lt=MAX_IMAGES), _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    """Remove the photo in a slot; the photos after it move up one slot."""
    p = _product_for_images(db, pid)
    urls = p.images
    if slot >= len(urls):
        raise HTTPException(404, "No photo in that slot")
    del urls[slot]
    p.set_images(urls)
    db.commit()
    return _images_out(p)


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


# ---- "Frequently bought together": which other products are suggested next to a product
MAX_BUNDLE = 8


class BundleIn(BaseModel):
    product_ids: list[int] = Field(default_factory=list, max_length=MAX_BUNDLE)


def _bundle_out(p: models.Product) -> dict:
    return {"product_ids": [r.paired_id for r in p.pairings],
            "items": [{"id": r.paired.id, "name": r.paired.name, "category": r.paired.category.name if r.paired.category else None,
                       "image_url": r.paired.image_url, "price": r.paired.base_price, "is_available": r.paired.is_available} for r in p.pairings]}


def _bundle_product(db: Session, pid: int) -> models.Product:
    p = db.scalar(select(models.Product).where(models.Product.id == pid).options(
        selectinload(models.Product.pairings).selectinload(models.ProductPairing.paired).selectinload(models.Product.category)))
    if p is None:
        raise HTTPException(404, "Product not found")
    return p


@router.get("/products/{pid}/bundle")
def get_bundle(pid: int, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    return _bundle_out(_bundle_product(db, pid))


@router.put("/products/{pid}/bundle")
def put_bundle(pid: int, data: BundleIn, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    """Replace the suggestions of a product (the order you send is the order shown). Any product can be suggested next to any other."""
    p = _bundle_product(db, pid)
    ids = list(dict.fromkeys(data.product_ids))  # no duplicates, keep order
    if pid in ids:
        raise HTTPException(422, "A product cannot be suggested next to itself")
    found = set(db.scalars(select(models.Product.id).where(models.Product.id.in_(ids))).all()) if ids else set()
    if found != set(ids):
        raise HTTPException(422, "Unknown product in the list")
    by_paired = {r.paired_id: r for r in p.pairings}
    for r in list(p.pairings):
        if r.paired_id not in ids:
            p.pairings.remove(r)  # delete-orphan
    for n, i in enumerate(ids):
        if i in by_paired:
            by_paired[i].sort_order = n
        else:
            p.pairings.append(models.ProductPairing(paired_id=i, sort_order=n))
    db.commit()
    return _bundle_out(_bundle_product(db, pid))


# ---- free waffle toppings (the admin adds / renames / hides / reorders / deletes them; ice-cream flavours come from the Ice Cream category) ----
class ToppingIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    is_available: bool = True
    kind: Literal["TOPPING", "ICECREAM"] = "TOPPING"


class ToppingPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    is_available: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=1000)


def _topping_out(t: models.WaffleTopping) -> dict:
    return {"id": t.id, "name": t.name, "image_url": t.image_url, "is_available": t.is_available, "sort_order": t.sort_order, "kind": t.kind}


def _topping(db: Session, tid: int) -> models.WaffleTopping:
    t = db.get(models.WaffleTopping, tid)
    if t is None:
        raise HTTPException(404, "Topping not found")
    return t


@router.get("/waffle-toppings")
def list_toppings(_: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    return [_topping_out(t) for t in db.scalars(select(models.WaffleTopping).order_by(models.WaffleTopping.sort_order, models.WaffleTopping.id))]


@router.post("/waffle-toppings", status_code=201)
def create_topping(data: ToppingIn, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    name = data.name.strip()
    if not name:
        raise HTTPException(422, "Give the topping a name")
    last = db.scalar(select(func.max(models.WaffleTopping.sort_order))) or 0
    t = models.WaffleTopping(name=name, is_available=data.is_available, sort_order=last + 1, kind=data.kind)
    db.add(t)
    db.commit()
    return _topping_out(t)


@router.patch("/waffle-toppings/{tid}")
def patch_topping(tid: int, data: ToppingPatch, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    t = _topping(db, tid)
    if data.name is not None:
        if not data.name.strip():
            raise HTTPException(422, "Give the topping a name")
        t.name = data.name.strip()
    if data.is_available is not None:
        t.is_available = data.is_available
    if data.sort_order is not None:
        t.sort_order = data.sort_order
    db.commit()
    return _topping_out(t)


@router.delete("/waffle-toppings/{tid}")
def delete_topping(tid: int, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    db.delete(_topping(db, tid))  # past orders keep the text they were placed with
    db.commit()
    return {"ok": True}


@router.post("/waffle-toppings/{tid}/image")
async def topping_image(tid: int, file: UploadFile = File(...), _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    t = _topping(db, tid)
    t.image_url = f"/media/products/{await save_upload(file, 'products', 3, webp_only=True)}"
    db.commit()
    return _topping_out(t)


@router.delete("/waffle-toppings/{tid}/image")
def topping_image_delete(tid: int, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    t = _topping(db, tid)
    t.image_url = None
    db.commit()
    return _topping_out(t)