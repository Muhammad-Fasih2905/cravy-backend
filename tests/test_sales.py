from datetime import datetime, timezone

from sqlalchemy import select

from app import models
from test_api import ORDER, _admin_headers, client, ids  # noqa: F401  (client is a pytest fixture)


def _complete(c, S, completed_at):
    """Place a real order through the API, then mark it completed at an exact moment (UTC)."""
    pid, vids, _ = ids(S, SLUG)
    r = c.post("/api/orders", json={**ORDER, "items": [{"product_id": pid, "variant_id": vids[0] if vids else None, "addon_ids": [], "quantity": 3}]},
               headers={"Idempotency-Key": f"k-{completed_at.isoformat()}"})
    assert r.status_code in (200, 201), r.text
    with S() as s:
        o = s.scalar(select(models.Order).where(models.Order.order_number == r.json()["order_number"]))
        o.status, o.completed_at = "COMPLETED", completed_at
        s.commit()
        return o.total


SLUG = "demo-vanilla-scoop"


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_sales_report(client):
    c, S = client
    h = _admin_headers(c, S)
    assert c.get("/api/admin/sales").status_code == 401  # admin only

    empty = c.get("/api/admin/sales", headers=h).json()
    assert empty["first_sale"] is None and empty["total"] == 0 and empty["mode"] == "month"

    a = _complete(c, S, utc(2025, 12, 31, 23, 30))  # 04:30 on 1 Jan in Pakistan -> counts for January 2026
    b = _complete(c, S, utc(2026, 9, 10, 12, 0))
    d = _complete(c, S, utc(2026, 9, 10, 20, 30))  # 01:30 on 11 Sep in Pakistan -> counts for the 11th
    # a pending order must never count as received money
    pid, vids, _ = ids(S, SLUG)
    c.post("/api/orders", json={**ORDER, "items": [{"product_id": pid, "variant_id": vids[0] if vids else None, "addon_ids": [], "quantity": 3}]}, headers={"Idempotency-Key": "pending-1"})

    sep = c.get("/api/admin/sales?year=2026&month=9", headers=h).json()
    assert sep["mode"] == "month" and len(sep["points"]) == 30
    by = {p["key"]: p for p in sep["points"]}
    assert by["2026-09-10"]["total"] == b and by["2026-09-11"]["total"] == d
    assert sep["total"] == b + d and sep["orders"] == 2 and sep["best"]["total"] == max(b, d)
    assert sep["first_sale"] == {"year": 2026, "month": 1}
    assert sep["years"][0] == 2026 and sep["previous_total"] == 0 and sep["change_percent"] is None

    year = c.get("/api/admin/sales?year=2026", headers=h).json()
    assert year["mode"] == "year" and [p["label"] for p in year["points"]][:3] == ["Jan", "Feb", "Mar"]
    assert year["points"][0]["total"] == a and year["points"][8]["total"] == b + d and year["total"] == a + b + d
    assert c.get("/api/admin/sales?year=2026&month=0", headers=h).json()["mode"] == "year"

    # the month after September compares itself with September
    octo = c.get("/api/admin/sales?year=2026&month=10", headers=h).json()
    assert octo["total"] == 0 and octo["previous_total"] == b + d
    assert c.get("/api/admin/sales?days=7", headers=h).json()["mode"] == "days"
    assert len(c.get("/api/admin/sales?days=7", headers=h).json()["points"]) == 7
    assert c.get("/api/admin/sales?month=13", headers=h).status_code == 422


def test_running_month_is_compared_with_the_same_days(client):
    c, S = client
    h = _admin_headers(c, S)
    now = datetime.now(timezone.utc)
    cur = c.get("/api/admin/sales", headers=h).json()
    assert cur["partial"] is True and cur["year"] == now.year
    assert c.get("/api/admin/sales?year=2026&month=9", headers=h).json()["partial"] is False


def test_one_day_view_lists_the_orders(client):
    c, S = client
    h = _admin_headers(c, S)
    x = _complete(c, S, utc(2026, 9, 10, 9, 5))    # 14:05 in Pakistan
    y = _complete(c, S, utc(2026, 9, 10, 20, 30))  # 01:30 on the 11th in Pakistan -> not this day
    day = c.get("/api/admin/sales?date=2026-09-10", headers=h).json()
    assert day["mode"] == "day" and day["date"] == "2026-09-10" and len(day["points"]) == 24
    assert day["total"] == x and day["orders"] == 1 and day["points"][14]["total"] == x and day["points"][14]["label"] == "2p"
    assert [o["time"] for o in day["orders_list"]] == ["14:05"] and day["orders_list"][0]["total"] == x and day["orders_list"][0]["method"] == "COD"
    assert day["best"]["total"] == x and day["partial"] is False
    nxt = c.get("/api/admin/sales?date=2026-09-11", headers=h).json()
    assert nxt["total"] == y and nxt["points"][1]["total"] == y and nxt["previous_total"] == x
    assert c.get("/api/admin/sales?date=nonsense", headers=h).status_code == 422