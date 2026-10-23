import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import models
from ..db import get_db
from ..pricing import build_quote, get_settings
from ..notify import notify_new_order
from ..schemas import OrderIn, OrderOut, QuoteIn, QuoteOut
from ..uploads import save_upload

router = APIRouter(prefix="/api", tags=["orders"])


def _out(o: models.Order) -> OrderOut:
    return OrderOut(
        order_number=o.order_number, status=o.status, subtotal=o.subtotal, delivery_charge=o.delivery_charge,
        discount_total=o.discount_total, tax_total=o.tax_total, total=o.total,
        payment_method=o.payment.method, payment_status=o.payment.status,
    )


@router.post("/cart/quote", response_model=QuoteOut)
def quote(data: QuoteIn, db: Session = Depends(get_db)):
    q, _ = build_quote(db, data)
    return q


@router.post("/orders", response_model=OrderOut, status_code=201)
def create_order(data: OrderIn, background: BackgroundTasks, idempotency_key: str = Header(min_length=8, max_length=80), db: Session = Depends(get_db)):
    existing = db.scalar(select(models.Order).where(models.Order.idempotency_key == idempotency_key))
    if existing:  # retry of the same request: return the same order, never a duplicate
        return _out(existing)

    q, coupon = build_quote(db, data, require_zone=True)
    s = get_settings(db)
    if not q.meets_minimum:
        raise HTTPException(422, f"Minimum order is Rs. {q.min_order_value}")
    enabled = {"COD": s.cod_enabled, "EASYPAISA": s.easypaisa_enabled, "BANK_TRANSFER": s.bank_enabled}
    if not enabled[data.payment_method]:
        raise HTTPException(422, "This payment method is not available")

    order = models.Order(
        order_number=f"CRV-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(3).upper()}",
        idempotency_key=idempotency_key,
        customer_name=data.customer_name, customer_phone=data.customer_phone, customer_email=data.customer_email,
        city=data.city, address=data.address, landmark=data.landmark, instructions=data.instructions,
        subtotal=q.subtotal, delivery_charge=q.delivery_charge, discount_total=q.discount_total,
        tax_total=q.tax_total, delivery_zone=q.zone_name, total=q.total, coupon_code=q.coupon_code,
        items=[
            models.OrderItem(
                product_id=l.product_id, product_name=l.product_name, variant_name=l.variant_name,
                addons=l.addons, unit_price=l.unit_price, quantity=l.quantity, line_total=l.line_total,
            ) for l in q.lines
        ],
        payment=models.Payment(
            method=data.payment_method, amount=q.total,
            status="UNPAID" if data.payment_method == "COD" else "PENDING_VERIFICATION",
        ),
    )
    if coupon:
        coupon.used_count += 1
    db.add(order)
    try:
        db.commit()
    except IntegrityError:  # concurrent duplicate with the same key
        db.rollback()
        dup = db.scalar(select(models.Order).where(models.Order.idempotency_key == idempotency_key))
        if dup:
            return _out(dup)
        raise HTTPException(409, "Could not create order, please retry")
    background.add_task(notify_new_order, order.id)
    return _out(order)


@router.get("/orders/{order_number}", response_model=OrderOut)
def get_order(order_number: str, db: Session = Depends(get_db)):
    o = db.scalar(select(models.Order).where(models.Order.order_number == order_number))
    if o is None:
        raise HTTPException(404, "Order not found")
    return _out(o)


class PaymentRefIn(BaseModel):
    transaction_ref: str = Field(min_length=4, max_length=120)
    phone: str = Field(min_length=10, max_length=20)


@router.post("/orders/{order_number}/payment-reference", response_model=OrderOut)
def submit_payment_reference(order_number: str, data: PaymentRefIn, db: Session = Depends(get_db)):
    """Customer submits their Easypaisa/bank transaction id. Stays PENDING_VERIFICATION until an admin confirms."""
    o = db.scalar(select(models.Order).where(models.Order.order_number == order_number))
    if o is None or o.customer_phone.strip() != data.phone.strip():
        raise HTTPException(404, "Order not found")
    if o.payment.method == "COD" or o.payment.status == "PAID":
        raise HTTPException(422, "No payment reference needed for this order")
    o.payment.transaction_ref = data.transaction_ref.strip()
    o.payment.status = "PENDING_VERIFICATION"
    db.commit()
    return _out(o)


@router.post("/orders/{order_number}/receipt", response_model=OrderOut)
async def upload_receipt(order_number: str, phone: str = Form(min_length=10, max_length=20), file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Private receipt upload (JPG/PNG/WEBP/PDF, max 5 MB). Only an admin can view it."""
    o = db.scalar(select(models.Order).where(models.Order.order_number == order_number))
    if o is None or o.customer_phone.strip() != phone.strip():
        raise HTTPException(404, "Order not found")
    if o.payment.method == "COD" or o.payment.status == "PAID":
        raise HTTPException(422, "No receipt needed for this order")
    o.payment.receipt_path = await save_upload(file, "receipts", 5, allow_pdf=True)
    o.payment.status = "PENDING_VERIFICATION"
    db.commit()
    return _out(o)
