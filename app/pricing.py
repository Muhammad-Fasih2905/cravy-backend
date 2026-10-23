"""Server-side pricing. The client's prices are never trusted: only ids and quantities."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import models
from .schemas import QuoteIn, QuoteLine, QuoteOut


def get_settings(db: Session) -> models.BusinessSettings:
    s = db.get(models.BusinessSettings, 1)
    if s is None:
        s = models.BusinessSettings(id=1)
        db.add(s)
        db.flush()
    return s


def calc_discount(coupon: models.Coupon, subtotal: int) -> int:
    if coupon.type == "PERCENT":
        d = subtotal * coupon.value // 100
        if coupon.max_discount is not None:
            d = min(d, coupon.max_discount)
    else:
        d = coupon.value
    return max(0, min(d, subtotal))


def load_coupon(db: Session, code: str, subtotal: int) -> models.Coupon:
    c = db.scalar(select(models.Coupon).where(models.Coupon.code == code.strip().upper()))
    if c is None or not c.is_active:
        raise HTTPException(422, "Invalid promo code")
    if c.expires_at and c.expires_at.replace(tzinfo=c.expires_at.tzinfo or timezone.utc) < datetime.now(timezone.utc):
        raise HTTPException(422, "Promo code has expired")
    if c.usage_limit is not None and c.used_count >= c.usage_limit:
        raise HTTPException(422, "Promo code usage limit reached")
    if c.min_subtotal and subtotal < c.min_subtotal:
        raise HTTPException(422, f"Promo code needs a minimum order of Rs. {c.min_subtotal}")
    return c


def build_quote(db: Session, data: QuoteIn, require_zone: bool = False) -> tuple[QuoteOut, models.Coupon | None]:
    ids = {i.product_id for i in data.items}
    products = {
        p.id: p
        for p in db.scalars(
            select(models.Product)
            .where(models.Product.id.in_(ids))
            .options(selectinload(models.Product.variants), selectinload(models.Product.addons))
        )
    }
    lines: list[QuoteLine] = []
    for item in data.items:
        p = products.get(item.product_id)
        if p is None or not p.is_available:
            raise HTTPException(422, f"Product {item.product_id} is unavailable")
        variant = None
        if p.variants:
            variant = next((v for v in p.variants if v.id == item.variant_id and v.is_available), None)
            if variant is None:
                raise HTTPException(422, f"Choose a valid size for {p.name}")
        elif item.variant_id is not None:
            raise HTTPException(422, f"{p.name} has no sizes")
        chosen = []
        for aid in set(item.addon_ids):
            a = next((x for x in p.addons if x.id == aid and x.is_available), None)
            if a is None:
                raise HTTPException(422, f"Invalid add-on for {p.name}")
            chosen.append({"name": a.name, "price": a.price})
        unit = (variant.price if variant else p.base_price) + sum(a["price"] for a in chosen)
        lines.append(QuoteLine(
            product_id=p.id, product_name=p.name, variant_name=variant.name if variant else None,
            addons=chosen, unit_price=unit, quantity=item.quantity, line_total=unit * item.quantity,
        ))
    subtotal = sum(l.line_total for l in lines)
    s = get_settings(db)
    coupon, discount = None, 0
    if data.coupon_code:
        coupon = load_coupon(db, data.coupon_code, subtotal)
        discount = calc_discount(coupon, subtotal)
    zones = db.scalars(select(models.DeliveryZone).where(models.DeliveryZone.is_active)).all()
    zone, delivery, pending, min_order = None, s.delivery_charge, False, s.min_order_value
    if zones:  # delivery areas are configured, so the customer must pick one
        zone = next((z for z in zones if z.id == data.zone_id), None)
        if zone is None:
            if require_zone:
                raise HTTPException(422, "Please choose a delivery area")
            delivery, pending = 0, True
        else:
            delivery = 0 if zone.free_above is not None and subtotal >= zone.free_above else zone.charge
            min_order = zone.min_order if zone.min_order is not None else s.min_order_value
    tax = ((subtotal - discount) * s.tax_percent + 50) // 100
    total = subtotal - discount + delivery + tax
    return QuoteOut(
        lines=lines, subtotal=subtotal, delivery_charge=delivery, discount_total=discount, tax_total=tax,
        total=total, coupon_code=coupon.code if coupon else None, meets_minimum=subtotal >= min_order,
        min_order_value=min_order, delivery_pending=pending, zone_name=zone.name if zone else None,
    ), coupon
