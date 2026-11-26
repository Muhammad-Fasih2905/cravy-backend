"""Server-side pricing. The client's prices are never trusted: only ids and quantities."""
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import models
from .config import settings as cfg
from .delivery import MAX_KM, band_label, charge_for, haversine_km
from .discounts import apply_discount
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


def is_waffle(p: models.Product) -> bool:
    slug = (p.category.slug if p.category else "").lower().replace("-", "").replace("_", "")
    return slug in ("waffle", "waffles")


def scoops_for(variant_name: str | None, addon_names: list[str]) -> tuple[int, bool]:
    """How many free ice-cream scoops this waffle choice includes: read from the chosen size / add-on name,
    e.g. "With Ice Cream (4 Scoops)" -> (4, True), "Without Ice Cream" -> (0, True). A waffle that mentions neither
    gets (1, False): one scoop that the customer may take or skip."""
    names = [n.lower() for n in ([variant_name] if variant_name else []) + addon_names]
    for n in names:
        m = re.search(r"(\d+)\s*scoops?", n)
        if m:
            return int(m.group(1)), True
    if any("without" in n or "no ice" in n for n in names):
        return 0, True
    return 1, False


def _free_choices(db: Session, p: models.Product, item, scoops: int, exact: bool) -> list[dict]:
    """Validate the customer's free toppings / ice-cream flavours and return them as price-0 add-on lines."""
    out: list[dict] = []
    if not is_waffle(p):
        if item.toppings or item.flavours:
            raise HTTPException(422, f"{p.name} has no toppings or ice-cream choices")
        return out
    ids = list(dict.fromkeys(item.toppings))
    if ids:
        rows = {t.id: t for t in db.scalars(select(models.WaffleTopping).where(models.WaffleTopping.id.in_(ids)))}
        for tid in ids:
            t = rows.get(tid)
            if t is None or not t.is_available:
                raise HTTPException(422, "A topping you chose is not available any more")
            out.append({"name": f"Topping: {t.name}", "price": 0})
    picks: dict[int, int] = {}
    for f in item.flavours:
        picks[f.id] = picks.get(f.id, 0) + f.qty
    total = sum(picks.values())
    if scoops == 0:
        if total:
            raise HTTPException(422, "This waffle comes without ice cream")
    elif (total != scoops) if exact else (total > scoops):
        raise HTTPException(422, f"Please choose {'exactly ' if exact else 'up to '}{scoops} ice-cream scoop{'s' if scoops != 1 else ''} for {p.name}")
    if picks:
        W = models.WaffleTopping
        prods = {x.id: x for x in db.scalars(select(W).where(W.id.in_(list(picks)), W.kind == "ICECREAM"))}
        for fid, qty in picks.items():
            x = prods.get(fid)
            if x is None or not x.is_available:
                raise HTTPException(422, "An ice-cream flavour you chose is not available any more")
            out.append({"name": f"Ice cream: {x.name}" + (f" x{qty}" if qty > 1 else ""), "price": 0})
    return out


def build_quote(db: Session, data: QuoteIn, require_location: bool = False) -> tuple[QuoteOut, models.Coupon | None]:
    ids = {i.product_id for i in data.items}
    products = {
        p.id: p
        for p in db.scalars(
            select(models.Product)
            .where(models.Product.id.in_(ids))
            .options(selectinload(models.Product.variants), selectinload(models.Product.addons), selectinload(models.Product.category))
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
        chosen += _free_choices(db, p, item, *scoops_for(variant.name if variant else None, [a["name"] for a in chosen]))
        unit = apply_discount(variant.price if variant else p.base_price, p.discount_percent) + sum(a["price"] for a in chosen)
        lines.append(QuoteLine(
            product_id=p.id, product_name=p.name, variant_name=variant.name if variant else None,
            addons=chosen, unit_price=unit, quantity=item.quantity, line_total=unit * item.quantity,
            discount_percent=p.discount_percent or 0,
        ))
    subtotal = sum(l.line_total for l in lines)
    s = get_settings(db)
    coupon, discount = None, 0
    if data.coupon_code:
        coupon = load_coupon(db, data.coupon_code, subtotal)
        discount = calc_discount(coupon, subtotal)
    min_order = s.min_order_value
    delivery, pending, deliverable, message = 0, True, True, None
    km = band = None
    if data.latitude is not None and data.longitude is not None:
        shop_lat = s.shop_latitude if s.shop_latitude is not None else cfg.shop_latitude
        shop_lng = s.shop_longitude if s.shop_longitude is not None else cfg.shop_longitude
        if shop_lat is None or shop_lng is None:
            raise HTTPException(503, "Delivery is not configured yet: the shop location is missing")
        km = round(haversine_km(shop_lat, shop_lng, data.latitude, data.longitude), 2)
        charge = charge_for(km)
        if charge is None:
            deliverable, message = False, f"Sorry, we only deliver within {MAX_KM} km of the shop"
            if require_location:
                raise HTTPException(422, message)
        else:
            delivery, pending, band = charge, False, band_label(km)
    elif require_location:
        raise HTTPException(422, "Please share your delivery location on the map")
    tax = ((subtotal - discount) * s.tax_percent + 50) // 100
    total = subtotal - discount + delivery + tax
    return QuoteOut(
        lines=lines, subtotal=subtotal, delivery_charge=delivery, discount_total=discount, tax_total=tax,
        total=total, coupon_code=coupon.code if coupon else None, meets_minimum=subtotal >= min_order,
        min_order_value=min_order, delivery_pending=pending, distance_km=km, delivery_band=band,
        deliverable=deliverable, delivery_message=message,
    ), coupon