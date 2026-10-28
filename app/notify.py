import logging
import smtplib
from email.message import EmailMessage

from . import models
from .config import settings
from .db import SessionLocal
from .models import now
from .payments import instructions_for
from .pricing import get_settings

log = logging.getLogger(__name__)


def _send(to: str, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.email_from or settings.smtp_user or "no-reply@cravy.local", to, subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
        s.starttls()
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_pass or "")
        s.send_message(msg)


def notify_new_order(order_id: int) -> None:
    """Runs after the response is sent. Does nothing unless SMTP is configured; never claims success without it."""
    if not settings.smtp_host:
        return
    with SessionLocal() as db:
        o = db.get(models.Order, order_id)
        if o is None:
            return
        items = "\n".join(f"- {i.quantity}x {i.product_name}" + (f" ({i.variant_name})" if i.variant_name else "") for i in o.items)
        summary = f"Order {o.order_number}\n{items}\nTotal: Rs. {o.total}\nPayment: {o.payment.method}\nDeliver to: {o.address}, {o.city}"
        pay = instructions_for(o, get_settings(db))
        if pay:
            summary += f"\n\n{pay.message}"
        jobs = []
        if o.customer_email:
            jobs.append((o.customer_email, "customer_confirmation", f"Your Cravy order {o.order_number}", f"Thank you for ordering from Cravy!\n\n{summary}"))
        if settings.admin_notify_email:
            jobs.append((settings.admin_notify_email, "admin_new_order", f"New order {o.order_number}", f"{o.customer_name} {o.customer_phone}\n\n{summary}"))
        for to, tpl, subject, body in jobs:
            row = models.NotificationLog(order_id=o.id, channel="EMAIL", recipient=to, template=tpl)
            db.add(row)
            try:
                _send(to, subject, body)
                row.status, row.sent_at = "SENT", now()
            except Exception as e:  # noqa: BLE001
                row.status, row.error = "FAILED", str(e)[:300]
                log.warning("Email to %s failed: %s", to, e)
        db.commit()


def notify_payment_submitted(order_id: int) -> None:
    """Tells the owner a customer sent Easypaisa proof that needs checking. No-op unless SMTP is configured."""
    if not settings.smtp_host or not settings.admin_notify_email:
        return
    with SessionLocal() as db:
        o = db.get(models.Order, order_id)
        if o is None:
            return
        body = (f"Order {o.order_number} ({o.customer_name}, {o.customer_phone})\n"
                f"Amount to verify: Rs. {o.payment.amount}\nTransaction ID: {o.payment.transaction_ref or 'not given'}\n"
                f"Screenshot uploaded: {'yes' if o.payment.receipt_path else 'no'}\n\n"
                "Check it in your Easypaisa app, then mark the payment PAID in the admin panel.")
        row = models.NotificationLog(order_id=o.id, channel="EMAIL", recipient=settings.admin_notify_email, template="admin_payment_submitted")
        db.add(row)
        try:
            _send(settings.admin_notify_email, f"Verify payment for {o.order_number}", body)
            row.status, row.sent_at = "SENT", now()
        except Exception as e:  # noqa: BLE001
            row.status, row.error = "FAILED", str(e)[:300]
            log.warning("Payment email failed: %s", e)
        db.commit()
