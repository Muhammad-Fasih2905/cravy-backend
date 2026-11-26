"""LOCAL TESTING ONLY: adds fake COMPLETED orders for last month and this month, so the dashboard sales chart has data to show.

    python -m app.seed_sales_demo           # add them (running it twice never doubles them)
    python -m app.seed_sales_demo --clear   # remove them again

Every fake order is numbered DEMO-00001... and named "Demo Customer", so it is easy to spot and to delete.
It refuses to touch a live/remote database. It needs the menu to exist: start the backend once first."""
import random
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from . import models
from .config import settings
from .db import SessionLocal, _url

SHOP_TZ = timezone(timedelta(hours=5))  # Pakistan time, same as the sales chart
HOURS = [11, 12, 13, 13, 14, 14, 15, 17, 18, 19, 20, 20, 21, 21, 22, 23]  # lunch + dinner rush, a little late night


def _is_local() -> bool:
    u = make_url(_url)
    return u.get_backend_name() == "sqlite" or (u.host or "") in ("localhost", "127.0.0.1", "::1")


def clear(db) -> int:
    rows = db.scalars(select(models.Order).where(models.Order.order_number.like("DEMO-%"))).all()
    for o in rows:
        db.delete(o)
    db.commit()
    return len(rows)


def add(db) -> tuple[int, int]:
    if db.scalar(select(func.count(models.Order.id)).where(models.Order.order_number.like("DEMO-%"))):
        raise SystemExit("Demo sales are already there. Run with --clear first if you want fresh ones.")
    products = db.scalars(select(models.Product).where(models.Product.is_available.is_(True))).all()
    if not products:
        raise SystemExit("No products found. Start the backend once (uvicorn app.main:app) so the menu loads, then run this again.")
    rnd = random.Random(2026)
    now = datetime.now(SHOP_TZ)
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    first = (first - timedelta(days=1)).replace(day=1)  # the 1st of LAST month
    n, money, day = 0, 0, first
    while day.date() <= now.date():
        weekend = day.weekday() in (4, 5, 6)  # Fri, Sat, Sun are busier
        count = 0 if rnd.random() < 0.06 else rnd.randint(5, 13) if weekend else rnd.randint(2, 7)
        for _ in range(count):
            at = day.replace(hour=rnd.choice(HOURS), minute=rnd.randint(0, 59))
            if at > now:
                continue
            n += 1
            picks = rnd.sample(products, k=min(len(products), rnd.choice([1, 1, 2, 3])))
            items = [models.OrderItem(product_id=p.id, product_name=p.name, addons=[], unit_price=p.base_price,
                                      quantity=(q := rnd.choice([1, 1, 1, 2, 3])), line_total=p.base_price * q) for p in picks]
            subtotal = sum(i.line_total for i in items)
            total = subtotal + rnd.choice([99, 99, 149, 199])
            when = at.astimezone(timezone.utc)
            method = "EASYPAISA" if rnd.random() < 0.4 else "COD"
            o = models.Order(
                order_number=f"DEMO-{n:05d}", idempotency_key=f"demo-{n}", status="COMPLETED", customer_name="Demo Customer",
                customer_phone="03000000000", city="Karachi", address="Demo address (test data)", subtotal=subtotal,
                delivery_charge=total - subtotal, discount_total=0, tax_total=0, total=total,
                created_at=when - timedelta(minutes=rnd.randint(25, 55)), updated_at=when, completed_at=when, items=items,
                payment=models.Payment(method=method, status="PAID", amount=total, transaction_ref=f"DEMO{n:08d}" if method == "EASYPAISA" else None),
            )
            db.add(o)
            money += total
        day += timedelta(days=1)
    db.commit()
    return n, money


def main() -> None:
    if settings.is_production or not _is_local():
        raise SystemExit("Refused: this is for your local test database only, never the live one.")
    try:
        with SessionLocal() as db:
            if "--clear" in sys.argv:
                print(f"Removed {clear(db)} demo orders.")
                return
            n, money = add(db)
            _report(n, money)
    except DBAPIError:
        raise SystemExit("The database has no tables yet. Start the backend once (uvicorn app.main:app --reload), stop it, then run this again.")


def _report(n: int, money: int) -> None:
    print(f"Added {n} demo completed orders (Rs. {money:,}) for last month and this month. Remove them any time: python -m app.seed_sales_demo --clear")


if __name__ == "__main__":
    main()