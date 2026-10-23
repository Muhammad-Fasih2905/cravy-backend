import re
import secrets
import time
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import models
from ..db import get_db
from ..config import settings as cfg
from ..pricing import get_settings
from ..uploads import save_upload
from ..security import get_admin, make_token, verify_password

router = APIRouter(prefix="/api/admin", tags=["admin"])
STATUSES = {"PENDING_CONFIRMATION", "CONFIRMED", "PREPARING", "READY_FOR_DISPATCH", "OUT_FOR_DELIVERY", "DELIVERED", "CANCELLED"}
PAY_STATUSES = {"UNPAID", "PENDING_VERIFICATION", "PAID", "FAILED", "REFUNDED"}
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


def _detail(o: models.Order) -> dict:
    return {
        "order_number": o.order_number, "status": o.status, "created_at": o.created_at.isoformat(),
        "customer_name": o.customer_name, "customer_phone": o.customer_phone, "customer_email": o.customer_email,
        "city": o.city, "address": o.address, "landmark": o.landmark, "instructions": o.instructions,
        "subtotal": o.subtotal, "delivery_charge": o.delivery_charge, "tax_total": o.tax_total, "delivery_zone": o.delivery_zone, "discount_total": o.discount_total, "total": o.total,
        "coupon_code": o.coupon_code,
        "items": [{"name": i.product_name, "variant": i.variant_name, "addons": i.addons, "quantity": i.quantity, "line_total": i.line_total} for i in o.items],
        "payment_method": o.payment.method, "payment_status": o.payment.status,
        "transaction_ref": o.payment.transaction_ref, "admin_note": o.payment.admin_note, "has_receipt": bool(o.payment.receipt_path),
    }


@router.get("/stats")
def stats(_: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    O = models.Order
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    count = lambda *w: db.scalar(select(func.count(O.id)).where(*w)) or 0  # noqa: E731
    recent = db.scalars(select(O).where(O.status != "CANCELLED", O.created_at >= start - timedelta(days=6))).all()
    daily: dict[str, int] = defaultdict(int)
    for o in recent:
        daily[o.created_at.date().isoformat()] += o.total
    top = db.execute(
        select(models.OrderItem.product_name, func.sum(models.OrderItem.quantity).label("q"))
        .group_by(models.OrderItem.product_name).order_by(desc("q")).limit(5)
    ).all()
    return {
        "total_orders": count(), "today_orders": count(O.created_at >= start),
        "total_sales": db.scalar(select(func.coalesce(func.sum(O.total), 0)).where(O.status != "CANCELLED")),
        "pending_orders": count(O.status == "PENDING_CONFIRMATION"), "completed_orders": count(O.status == "DELIVERED"),
        "pending_payments": db.scalar(select(func.count(models.Payment.id)).where(models.Payment.status == "PENDING_VERIFICATION")) or 0,
        "bestsellers": [{"name": n, "quantity": int(q)} for n, q in top],
        "daily_sales": [{"date": (start - timedelta(days=d)).date().isoformat(),
                         "total": daily.get((start - timedelta(days=d)).date().isoformat(), 0)} for d in range(6, -1, -1)],
    }


@router.get("/orders")
def orders(status: str | None = None, method: str | None = None, q: str | None = None,
           _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    stmt = select(models.Order).join(models.Payment).options(selectinload(models.Order.items), selectinload(models.Order.payment))
    if status: stmt = stmt.where(models.Order.status == status)
    if method: stmt = stmt.where(models.Payment.method == method)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(models.Order.order_number.ilike(like), models.Order.customer_phone.ilike(like), models.Order.customer_name.ilike(like)))
    return [_detail(o) for o in db.scalars(stmt.order_by(desc(models.Order.created_at)).limit(200))]


def _order(db: Session, n: str) -> models.Order:
    o = db.scalar(select(models.Order).where(models.Order.order_number == n))
    if o is None:
        raise HTTPException(404, "Order not found")
    return o


class StatusIn(BaseModel):
    status: str


@router.patch("/orders/{n}/status")
def set_status(n: str, data: StatusIn, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    if data.status not in STATUSES:
        raise HTTPException(422, "Invalid status")
    o = _order(db, n); o.status = data.status; db.commit()
    return _detail(o)


class PaymentIn(BaseModel):
    status: str
    note: str | None = Field(default=None, max_length=300)


@router.patch("/orders/{n}/payment")
def set_payment(n: str, data: PaymentIn, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    if data.status not in PAY_STATUSES:
        raise HTTPException(422, "Invalid payment status")
    o = _order(db, n); o.payment.status = data.status; o.payment.admin_note = data.note; db.commit()
    return _detail(o)


class SettingsIn(BaseModel):
    business_name: str | None = None
    phone: str | None = None
    announcement: str | None = None
    delivery_charge: int | None = Field(default=None, ge=0)
    min_order_value: int | None = Field(default=None, ge=0)
    tax_percent: int | None = Field(default=None, ge=0, le=100)
    cod_enabled: bool | None = None
    easypaisa_enabled: bool | None = None
    easypaisa_name: str | None = None
    easypaisa_number: str | None = None
    bank_enabled: bool | None = None
    bank_name: str | None = None
    bank_account_title: str | None = None
    bank_account_number: str | None = None
    bank_iban: str | None = None


def _settings_dict(s: models.BusinessSettings) -> dict:
    return {k: getattr(s, k) for k in SettingsIn.model_fields}


@router.get("/settings")
def get_admin_settings(_: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    s = get_settings(db); db.commit()
    return _settings_dict(s)


@router.put("/settings")
def put_admin_settings(data: SettingsIn, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    s = get_settings(db)
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
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
def get_receipt(n: str, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
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


# ---- delivery zones ----
class ZoneIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    city: str | None = Field(default=None, min_length=1, max_length=80)
    charge: int | None = Field(default=None, ge=0)
    free_above: int | None = Field(default=None, ge=0)
    min_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


def _zone(z: models.DeliveryZone) -> dict:
    return {"id": z.id, "name": z.name, "city": z.city, "charge": z.charge, "free_above": z.free_above, "min_order": z.min_order, "is_active": z.is_active}


@router.get("/zones")
def admin_zones(_: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    return [_zone(z) for z in db.scalars(select(models.DeliveryZone).order_by(models.DeliveryZone.city, models.DeliveryZone.name))]


@router.post("/zones", status_code=201)
def create_zone(data: ZoneIn, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    if not data.name or not data.city or data.charge is None:
        raise HTTPException(422, "Name, city and charge are required")
    z = models.DeliveryZone(name=data.name.strip(), city=data.city.strip(), charge=data.charge, free_above=data.free_above, min_order=data.min_order)
    db.add(z); db.commit()
    return _zone(z)


@router.patch("/zones/{zid}")
def patch_zone(zid: int, data: ZoneIn, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    z = db.get(models.DeliveryZone, zid)
    if z is None:
        raise HTTPException(404, "Zone not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(z, k, v)  # free_above / min_order may be set to null on purpose
    db.commit()
    return _zone(z)


@router.delete("/zones/{zid}")
def delete_zone(zid: int, _: models.AdminUser = Depends(get_admin), db: Session = Depends(get_db)):
    z = db.get(models.DeliveryZone, zid)
    if z is None:
        raise HTTPException(404, "Zone not found")
    db.delete(z); db.commit()
    return {"ok": True}
