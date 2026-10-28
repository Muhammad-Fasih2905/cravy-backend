"""Payment rules in one place. Two methods only:

* COD       - UNPAID until the rider hands over the cash, then PAID.
* EASYPAISA - customer pays the order total to our Easypaisa number, then sends the Transaction ID
              and/or a screenshot. AWAITING_PAYMENT -> PENDING_VERIFICATION -> PAID (admin confirms
              in their Easypaisa app) or FAILED (customer can resubmit).
"""
import re

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import models
from .config import settings as cfg
from .pricing import get_settings
from .schemas import PaymentInstructions

METHODS = ("COD", "EASYPAISA")
PK_MOBILE = re.compile(r"^(03\d{9}|\+?923\d{9})$")


def same_phone(a: str, b: str) -> bool:
    """03001234567, +92 300 1234567 and 0300-1234567 are the same phone."""
    x, y = re.sub(r"\D", "", a or ""), re.sub(r"\D", "", b or "")
    return len(x) >= 10 and len(y) >= 10 and x[-10:] == y[-10:]


def easypaisa_account(s: models.BusinessSettings) -> tuple[str, str | None]:
    number = re.sub(r"[\s-]", "", s.easypaisa_number or "") or cfg.easypaisa_number
    return number, (s.easypaisa_name or cfg.easypaisa_account_name)


def method_enabled(s: models.BusinessSettings, method: str) -> bool:
    return bool(s.cod_enabled) if method == "COD" else bool(s.easypaisa_enabled)


def initial_status(method: str) -> str:
    return "UNPAID" if method == "COD" else "AWAITING_PAYMENT"


def instructions_for(o: models.Order, s: models.BusinessSettings) -> PaymentInstructions | None:
    p = o.payment
    if p.method != "EASYPAISA" or p.status in ("PAID", "REFUNDED") or o.status == "CANCELLED":
        return None
    number, name = easypaisa_account(s)
    msg = (f"Send exactly Rs. {p.amount} to Easypaisa {number}" + (f" ({name})" if name else "")
           + f" and write {o.order_number} in the payment note. Then enter your Transaction ID or upload a screenshot "
             "so we can verify your payment. Your order is prepared once the payment is confirmed.")
    return PaymentInstructions(method="EASYPAISA", account_number=number, account_name=name,
                               amount=p.amount, reference=o.order_number, message=msg)


def ensure_payment_defaults(db: Session) -> None:
    """Idempotent. Creates the settings row if missing and fills in the Easypaisa number if it is blank."""
    s = get_settings(db)
    if not (s.easypaisa_number or "").strip():
        s.easypaisa_number, s.easypaisa_enabled = cfg.easypaisa_number, True
    if not s.easypaisa_name and cfg.easypaisa_account_name:
        s.easypaisa_name = cfg.easypaisa_account_name
    db.commit()


def require_payment_open(o: models.Order) -> None:
    """Customer may only send proof while an online payment is still outstanding."""
    if o.status == "CANCELLED":
        raise HTTPException(422, "This order was cancelled")
    if o.payment.method == "COD":
        raise HTTPException(422, "Cash on Delivery orders need no payment proof")
    if o.payment.status in ("PAID", "REFUNDED"):
        raise HTTPException(422, "This order is already paid")
