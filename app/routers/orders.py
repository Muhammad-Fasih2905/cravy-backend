import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..db import get_db
from ..notify import notify_new_order, notify_payment_submitted
from ..payments import easypaisa_account, initial_status, instructions_for, method_enabled, require_payment_open, same_phone
from ..pricing import build_quote, get_settings
from ..schemas import OrderIn, OrderOut, QuoteIn, QuoteOut
from ..uploads import save_upload

router = APIRouter(prefix="/api", tags=["orders"])


def _out(o: models.Order, s: models.BusinessSettings) -> OrderOut:
    return OrderOut(
        order_number=o.order_number, status=o.status, subtotal=o.subtotal, delivery_charge=o.delivery_charge,
        discount_total=o.discount_total, tax_total=o.tax_total, total=o.total,
        payment_method=o.payment.method, payment_status=o.payment.status,
        payment_instructions=instructions_for(o, s),
    )


def _customer_order(db: Session, order_number: str, phone: str) -> models.Order:
    """Customers prove it is their order with the phone number used at checkout."""
    o = db.scalar(select(models.Order).where(models.Order.order_number == order_number))
    if o is None or not same_phone(o.customer_phone, phone):
        raise HTTPException(404, "Order not found")
    return o


@router.get("/payment-methods")
def payment_methods(db: Session = Depends(get_db)):
    """What the checkout page should offer. Easypaisa includes the account the customer must pay to."""
    s = get_settings(db)
    db.commit()
    number, name = easypaisa_account(s)
    return [
        {"method": "COD", "label": "Cash on Delivery", "enabled": s.cod_enabled,
         "description": "Pay in cash when your order arrives."},
        {"method": "EASYPAISA", "label": "Easypaisa", "enabled": s.easypaisa_enabled,
         "account_number": number, "account_name": name,
         "description": f"Pay the order total to Easypaisa {number}, then submit your Transaction ID or a screenshot."},
    ]


@router.post("/cart/quote", response_model=QuoteOut)
def quote(data: QuoteIn, db: Session = Depends(get_db)):
    q, _ = build_quote(db, data)
    return q


@router.post("/orders", response_model=OrderOut, status_code=201)
def create_order(data: OrderIn, background: BackgroundTasks, idempotency_key: str = Header(min_length=8, max_length=80), db: Session = Depends(get_db)):
    s = get_settings(db)
    existing = db.scalar(select(models.Order).where(models.Order.idempotency_key == idempotency_key))
    if existing:  # retry of the same request: return the same order, never a duplicate
        return _out(existing, s)

    q, coupon = build_quote(db, data, require_zone=True)
    if not q.meets_minimum:
        raise HTTPException(422, f"Minimum order is Rs. {q.min_order_value}")
    if not method_enabled(s, data.payment_method):
        raise HTTPException(422, "This payment method is not available")

    order = models.Order(
        order_number=f"CRV-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(4).upper()}",
        idempotency_key=idempotency_key,
        customer_name=data.customer_name.strip(), customer_phone=data.customer_phone.strip(), customer_email=data.customer_email,
        city=data.city.strip(), address=data.address.strip(), landmark=data.landmark, instructions=data.instructions,
        subtotal=q.subtotal, delivery_charge=q.delivery_charge, discount_total=q.discount_total,
        tax_total=q.tax_total, delivery_zone=q.zone_name, total=q.total, coupon_code=q.coupon_code,
        items=[
            models.OrderItem(
                product_id=l.product_id, product_name=l.product_name, variant_name=l.variant_name,
                addons=l.addons, unit_price=l.unit_price, quantity=l.quantity, line_total=l.line_total,
            ) for l in q.lines
        ],
        payment=models.Payment(method=data.payment_method, amount=q.total, status=initial_status(data.payment_method)),
    )
    if coupon:  # atomic: two simultaneous orders cannot both take the last use of a coupon
        C = models.Coupon
        took = db.execute(
            update(C).where(C.id == coupon.id, or_(C.usage_limit.is_(None), C.used_count < C.usage_limit))
            .values(used_count=C.used_count + 1)
        ).rowcount
        if not took:
            db.rollback()
            raise HTTPException(422, "Promo code usage limit reached")
    db.add(order)
    try:
        db.commit()
    except IntegrityError:  # concurrent duplicate with the same key
        db.rollback()
        dup = db.scalar(select(models.Order).where(models.Order.idempotency_key == idempotency_key))
        if dup:
            return _out(dup, s)
        raise HTTPException(409, "Could not create order, please retry")
    background.add_task(notify_new_order, order.id)
    return _out(order, s)


@router.get("/orders/{order_number}", response_model=OrderOut)
def get_order(order_number: str, db: Session = Depends(get_db)):
    o = db.scalar(select(models.Order).where(models.Order.order_number == order_number))
    if o is None:
        raise HTTPException(404, "Order not found")
    s = get_settings(db)
    db.commit()
    return _out(o, s)


class PaymentRefIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    transaction_ref: str = Field(pattern=r"^[A-Za-z0-9-]{6,40}$")  # Easypaisa Transaction ID (TID)
    phone: str = Field(min_length=10, max_length=20)


@router.post("/orders/{order_number}/payment-reference", response_model=OrderOut)
def submit_payment_reference(order_number: str, data: PaymentRefIn, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Customer submits their Easypaisa Transaction ID. Stays PENDING_VERIFICATION until an admin confirms."""
    o = _customer_order(db, order_number, data.phone)
    require_payment_open(o)
    tid = data.transaction_ref.upper()
    used = db.scalar(select(models.Payment.id).where(models.Payment.transaction_ref == tid, models.Payment.order_id != o.id))
    if used:
        raise HTTPException(409, "This Transaction ID was already used for another order. Check it and try again.")
    o.payment.transaction_ref = tid
    o.payment.status = "PENDING_VERIFICATION"
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This Transaction ID was already used for another order. Check it and try again.")
    background.add_task(notify_payment_submitted, o.id)
    return _out(o, get_settings(db))


@router.post("/orders/{order_number}/receipt", response_model=OrderOut)
async def upload_receipt(order_number: str, background: BackgroundTasks, phone: str = Form(min_length=10, max_length=20), file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Private payment screenshot (JPG/PNG/WEBP/PDF, max 5 MB). Only an admin can view it."""
    o = _customer_order(db, order_number, phone)
    require_payment_open(o)
    o.payment.receipt_path = await save_upload(file, "receipts", 5, allow_pdf=True)
    o.payment.status = "PENDING_VERIFICATION"
    db.commit()
    background.add_task(notify_payment_submitted, o.id)
    return _out(o, get_settings(db))
