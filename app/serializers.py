from datetime import datetime, timezone

from . import models


def _iso(dt: datetime | None) -> str | None:
    """Same text whether the database hands back an aware (Postgres) or naive (SQLite) datetime, so a live event and a refresh always match."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()


def order_detail(o: models.Order) -> dict:
    """The one shape of an order that the admin API, the live stream and the slip all use."""
    p = o.payment
    return {
        "order_number": o.order_number, "status": o.status,
        "created_at": _iso(o.created_at),
        "updated_at": _iso(o.updated_at or o.created_at),
        "completed_at": _iso(o.completed_at),
        "customer_name": o.customer_name, "customer_phone": o.customer_phone, "customer_email": o.customer_email,
        "city": o.city, "address": o.address, "landmark": o.landmark, "instructions": o.instructions,
        "latitude": o.latitude, "longitude": o.longitude,
        "map_url": f"https://www.google.com/maps?q={o.latitude},{o.longitude}" if o.latitude is not None and o.longitude is not None else None,
        "distance_km": o.delivery_distance_km, "delivery_zone": o.delivery_zone,
        "subtotal": o.subtotal, "delivery_charge": o.delivery_charge, "tax_total": o.tax_total,
        "discount_total": o.discount_total, "total": o.total, "coupon_code": o.coupon_code,
        "items": [{"name": i.product_name, "variant": i.variant_name, "addons": i.addons, "quantity": i.quantity,
                   "unit_price": i.unit_price, "line_total": i.line_total} for i in o.items],
        "payment_method": p.method, "payment_status": p.status, "payment_amount": p.amount,
        "transaction_ref": p.transaction_ref, "admin_note": p.admin_note, "has_receipt": bool(p.receipt_path),
    }
