import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as appdb, models, seed
from app.main import app


@pytest.fixture()
def client(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(seed, "engine", eng)
    monkeypatch.setattr(seed, "SessionLocal", Session)
    seed.run()
    with Session() as s:  # enable COD only is default; enable bank for payment tests
        st = s.get(models.BusinessSettings, 1)
        st.bank_enabled = True
        s.commit()

    def _db():
        d = Session()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[appdb.get_db] = _db
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def ids(Session, slug):
    with Session() as s:
        from sqlalchemy import select
        p = s.scalar(select(models.Product).where(models.Product.slug == slug))
        return p.id, [v.id for v in p.variants], [a.id for a in p.addons]


ORDER = dict(customer_name="Ali Khan", customer_phone="03001234567", city="Karachi",
             address="House 1, Street 2", payment_method="COD")


def test_menu_preload(client):
    c, _ = client
    r = c.get("/api/menu")
    assert r.status_code == 200 and len(r.json()["categories"]) == 5


def test_quote_uses_server_prices(client):
    c, S = client
    pid, vids, aids = ids(S, "demo-vanilla-scoop")
    r = c.post("/api/cart/quote", json={"items": [{"product_id": pid, "variant_id": vids[1], "addon_ids": aids, "quantity": 2}]})
    assert r.status_code == 200
    # (350 + 40 + 30) * 2 = 840 ; + delivery 100
    assert r.json()["subtotal"] == 840 and r.json()["total"] == 940


def test_coupon_and_invalid_variant(client):
    c, S = client
    pid, vids, _ = ids(S, "demo-cold-drink")
    item = {"product_id": pid, "variant_id": vids[2], "quantity": 1}
    ok = c.post("/api/cart/quote", json={"items": [item], "coupon_code": "demo10"}).json()
    assert ok["discount_total"] == 38 and ok["total"] == 380 - 38 + 100
    assert c.post("/api/cart/quote", json={"items": [{"product_id": pid, "quantity": 1}]}).status_code == 422


def test_order_is_idempotent_and_payment_status(client):
    c, S = client
    pid, vids, _ = ids(S, "demo-cold-drink")
    body = {**ORDER, "items": [{"product_id": pid, "variant_id": vids[1], "quantity": 1}]}
    h = {"Idempotency-Key": "key-12345678"}
    a = c.post("/api/orders", json=body, headers=h)
    b = c.post("/api/orders", json=body, headers=h)
    assert a.status_code == 201 and a.json()["order_number"] == b.json()["order_number"]
    assert a.json()["payment_status"] == "UNPAID"
    bank = c.post("/api/orders", json={**body, "payment_method": "BANK_TRANSFER"}, headers={"Idempotency-Key": "key-87654321"})
    assert bank.json()["payment_status"] == "PENDING_VERIFICATION"
    easy = c.post("/api/orders", json={**body, "payment_method": "EASYPAISA"}, headers={"Idempotency-Key": "key-11111111"})
    assert easy.status_code == 422  # disabled until configured


def test_minimum_order(client):
    c, S = client
    pid, _, _ = ids(S, "demo-cone")
    r = c.post("/api/orders", json={**ORDER, "items": [{"product_id": pid, "quantity": 1}]}, headers={"Idempotency-Key": "key-22222222"})
    assert r.status_code == 422


def test_payment_reference_requires_matching_phone(client):
    c, S = client
    pid, vids, _ = ids(S, "demo-cold-drink")
    body = {**ORDER, "payment_method": "BANK_TRANSFER", "items": [{"product_id": pid, "variant_id": vids[1], "quantity": 1}]}
    n = c.post("/api/orders", json=body, headers={"Idempotency-Key": "key-33333333"}).json()["order_number"]
    bad = c.post(f"/api/orders/{n}/payment-reference", json={"transaction_ref": "TXN12345", "phone": "03009999999"})
    assert bad.status_code == 404
    ok = c.post(f"/api/orders/{n}/payment-reference", json={"transaction_ref": "TXN12345", "phone": ORDER["customer_phone"]})
    assert ok.status_code == 200 and ok.json()["payment_status"] == "PENDING_VERIFICATION"


def test_admin_requires_auth_and_manages_orders(client):
    from app.security import hash_password
    c, S = client
    with S() as s:
        s.add(models.AdminUser(email="a@x.com", password_hash=hash_password("pw-12345")))
        s.commit()
    assert c.get("/api/admin/orders").status_code == 401
    assert c.post("/api/admin/login", json={"email": "a@x.com", "password": "bad"}).status_code == 401
    tok = c.post("/api/admin/login", json={"email": "a@x.com", "password": "pw-12345"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    pid, vids, _ = ids(S, "demo-cold-drink")
    body = {**ORDER, "payment_method": "BANK_TRANSFER", "items": [{"product_id": pid, "variant_id": vids[1], "quantity": 2}]}
    n = c.post("/api/orders", json=body, headers={"Idempotency-Key": "key-44444444"}).json()["order_number"]
    assert c.patch(f"/api/admin/orders/{n}/status", json={"status": "CONFIRMED"}, headers=h).json()["status"] == "CONFIRMED"
    assert c.patch(f"/api/admin/orders/{n}/payment", json={"status": "PAID"}, headers=h).json()["payment_status"] == "PAID"
    st = c.get("/api/admin/stats", headers=h).json()
    assert st["total_orders"] == 1 and st["pending_payments"] == 0 and st["total_sales"] == 700
    assert c.get("/api/admin/orders?method=BANK_TRANSFER", headers=h).json()[0]["order_number"] == n


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_uploads_are_validated_and_receipts_private(client, tmp_path, monkeypatch):
    from app.config import settings as cfg
    from app.security import hash_password
    monkeypatch.setattr(cfg, "upload_dir", str(tmp_path))
    c, S = client
    with S() as s:
        s.add(models.AdminUser(email="a@x.com", password_hash=hash_password("pw-12345"))); s.commit()
    h = {"Authorization": "Bearer " + c.post("/api/admin/login", json={"email": "a@x.com", "password": "pw-12345"}).json()["token"]}
    pid, vids, _ = ids(S, "demo-cold-drink")
    assert c.post(f"/api/admin/products/{pid}/image", files={"file": ("x.png", PNG)}).status_code == 401
    assert c.post(f"/api/admin/products/{pid}/image", files={"file": ("x.png", b"not an image")}, headers=h).status_code == 422
    ok = c.post(f"/api/admin/products/{pid}/image", files={"file": ("x.png", PNG)}, headers=h)
    assert ok.status_code == 200 and ok.json()["image_url"].startswith("/media/products/")
    body = {**ORDER, "payment_method": "BANK_TRANSFER", "items": [{"product_id": pid, "variant_id": vids[1], "quantity": 1}]}
    n = c.post("/api/orders", json=body, headers={"Idempotency-Key": "key-55555555"}).json()["order_number"]
    assert c.post(f"/api/orders/{n}/receipt", data={"phone": "03000000000"}, files={"file": ("r.png", PNG)}).status_code == 404
    assert c.post(f"/api/orders/{n}/receipt", data={"phone": ORDER["customer_phone"]}, files={"file": ("r.png", PNG)}).status_code == 200
    assert c.get(f"/api/admin/orders/{n}/receipt").status_code == 401
    assert c.get(f"/api/admin/orders/{n}/receipt", headers=h).status_code == 200


def test_admin_creates_and_deletes_product(client):
    from app.security import hash_password
    c, S = client
    with S() as s:
        s.add(models.AdminUser(email="a@x.com", password_hash=hash_password("pw-12345"))); s.commit()
    h = {"Authorization": "Bearer " + c.post("/api/admin/login", json={"email": "a@x.com", "password": "pw-12345"}).json()["token"]}
    cat = c.get("/api/admin/categories", headers=h).json()[0]["id"]
    pid = c.post("/api/admin/products", json={"name": "Real Shake", "category_id": cat, "base_price": 450}, headers=h).json()["id"]
    assert any(p["name"] == "Real Shake" for cat in c.get("/api/menu").json()["categories"] for p in cat["products"])
    assert c.delete(f"/api/admin/products/{pid}", headers=h).status_code == 200


def _admin_headers(c, S):
    from app.security import hash_password
    with S() as s:
        s.add(models.AdminUser(email="a@x.com", password_hash=hash_password("pw-12345"))); s.commit()
    return {"Authorization": "Bearer " + c.post("/api/admin/login", json={"email": "a@x.com", "password": "pw-12345"}).json()["token"]}


def test_delivery_zones_and_tax(client):
    c, S = client
    h = _admin_headers(c, S)
    assert c.put("/api/admin/settings", json={"tax_percent": 5}, headers=h).json()["tax_percent"] == 5
    zid = c.post("/api/admin/zones", json={"name": "Clifton", "city": "Karachi", "charge": 150, "free_above": 1000}, headers=h).json()["id"]
    pid, vids, _ = ids(S, "demo-cold-drink")
    line = {"product_id": pid, "variant_id": vids[1], "quantity": 1}  # Rs. 300
    pending = c.post("/api/cart/quote", json={"items": [line]}).json()
    assert pending["delivery_pending"] is True and pending["tax_total"] == 15
    q = c.post("/api/cart/quote", json={"items": [line], "zone_id": zid}).json()
    assert q["delivery_charge"] == 150 and q["tax_total"] == 15 and q["total"] == 465
    free = c.post("/api/cart/quote", json={"items": [{**line, "quantity": 4}], "zone_id": zid}).json()
    assert free["delivery_charge"] == 0 and free["total"] == 1200 + 60  # 5% tax on 1200
    assert c.post("/api/orders", json={**ORDER, "items": [line]}, headers={"Idempotency-Key": "key-66666666"}).status_code == 422
    ok = c.post("/api/orders", json={**ORDER, "items": [line], "zone_id": zid}, headers={"Idempotency-Key": "key-77777777"})
    assert ok.status_code == 201 and ok.json()["tax_total"] == 15 and ok.json()["total"] == 465
    assert len(c.get("/api/zones").json()) == 1
    c.patch(f"/api/admin/zones/{zid}", json={"is_active": False}, headers=h)
    assert c.get("/api/zones").json() == []


def test_admin_edits_sizes_and_toppings(client):
    c, S = client
    h = _admin_headers(c, S)
    pid, vids, aids = ids(S, "demo-vanilla-scoop")
    cur = c.get(f"/api/admin/products/{pid}/options", headers=h).json()
    assert len(cur["variants"]) == 2 and len(cur["addons"]) == 2
    body = {
        "variants": [{"id": cur["variants"][0]["id"], "name": "One scoop", "price": 220}, {"name": "Triple scoop", "price": 480}],
        "addons": [{"id": cur["addons"][0]["id"], "name": "Nutella", "price": 60}],
    }
    new = c.put(f"/api/admin/products/{pid}/options", json=body, headers=h).json()
    assert [v["name"] for v in new["variants"]] == ["One scoop", "Triple scoop"]
    assert new["variants"][0]["id"] == vids[0]  # id kept, so saved carts stay valid
    assert [a["name"] for a in new["addons"]] == ["Nutella"]
    assert c.put(f"/api/admin/products/{pid}/options", json=body).status_code == 401
