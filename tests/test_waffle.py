from app import models, menu_seed
from tests.test_api import client, _admin_headers  # noqa  # noqa: F401


def _setup(Session):
    with Session() as db:
        w = models.Category(name="Waffle", slug="waffle")
        ic = models.Category(name="Ice Cream", slug="ice-cream")
        db.add_all([w, ic]); db.flush()
        wp = models.Product(category_id=w.id, name="Loaded", slug="loaded-w", base_price=549, variants=[
            models.Variant(name="With Ice Cream (2 Scoops)", price=549), models.Variant(name="Without Ice Cream", price=399)])
        mango = models.Product(category_id=ic.id, name="Mango", slug="mango-t", base_price=139)
        oreo = models.Product(category_id=ic.id, name="Oreo", slug="oreo-t", base_price=139)
        db.add_all([wp, mango, oreo]); db.flush()
        menu_seed.ensure_default_toppings(db)
        menu_seed.ensure_default_flavours(db)
        fl = {f.name: f.id for f in db.query(models.WaffleTopping).filter_by(kind='ICECREAM')}
        mango, oreo = fl['Mango'], fl['Oreo']
        return wp.id, [v.id for v in wp.variants], mango, oreo


def line(pid, vid, **kw):
    return {"product_id": pid, "variant_id": vid, "quantity": 1, **kw}


def test_free_choices(client):
    c, Session = client
    pid, (v2, v0), mango, oreo = _setup(Session)
    opts = c.get("/api/waffle-options").json()
    assert len(opts["toppings"]) == 5 and {f["name"] for f in opts["flavours"]} == {"Mango", "Oreo"}
    t = opts["toppings"][0]["id"]
    q = c.post("/api/cart/quote", json={"items": [line(pid, v2, toppings=[t], flavours=[{"id": mango, "qty": 1}, {"id": oreo, "qty": 1}])]}).json()
    assert q["subtotal"] == 549
    names = [a["name"] for a in q["lines"][0]["addons"]]
    assert names[0].startswith("Topping:") and "Ice cream: Mango" in names and "Ice cream: Oreo" in names
    assert all(a["price"] == 0 for a in q["lines"][0]["addons"])
    # same flavour twice, wrong count, no-ice-cream size
    ok = c.post("/api/cart/quote", json={"items": [line(pid, v2, flavours=[{"id": mango, "qty": 2}])]})
    assert ok.status_code == 200 and "Mango x2" in ok.json()["lines"][0]["addons"][0]["name"]
    assert c.post("/api/cart/quote", json={"items": [line(pid, v2, flavours=[{"id": mango, "qty": 1}])]}).status_code == 422
    assert c.post("/api/cart/quote", json={"items": [line(pid, v0, flavours=[{"id": mango, "qty": 1}])]}).status_code == 422
    assert c.post("/api/cart/quote", json={"items": [line(pid, v0, toppings=[t])]}).status_code == 200


def test_admin_manages_toppings(client):
    c, Session = client
    pid, (v2, v0), mango, oreo = _setup(Session)
    h = _admin_headers(c, Session)
    new = c.post("/api/admin/waffle-toppings", json={"name": "Nutella"}, headers=h).json()
    assert any(t["name"] == "Nutella" for t in c.get("/api/waffle-options").json()["toppings"])
    c.patch(f"/api/admin/waffle-toppings/{new['id']}", json={"is_available": False}, headers=h)
    assert all(t["name"] != "Nutella" for t in c.get("/api/waffle-options").json()["toppings"])
    assert c.post("/api/cart/quote", json={"items": [line(pid, v0, toppings=[new["id"]])]}).status_code == 422
    assert c.delete(f"/api/admin/waffle-toppings/{new['id']}", headers=h).status_code == 200
    # the admin adds and hides an ice-cream flavour himself
    fl = c.post("/api/admin/waffle-toppings", json={"name": "Kiwi", "kind": "ICECREAM"}, headers=h).json()
    assert fl["kind"] == "ICECREAM" and any(f["name"] == "Kiwi" for f in c.get("/api/waffle-options").json()["flavours"])
    assert all(t["name"] != "Kiwi" for t in c.get("/api/waffle-options").json()["toppings"])
    assert c.post("/api/cart/quote", json={"items": [line(pid, v2, flavours=[{"id": fl["id"], "qty": 2}])]}).status_code == 200
    c.patch(f"/api/admin/waffle-toppings/{fl['id']}", json={"is_available": False}, headers=h)
    assert c.post("/api/cart/quote", json={"items": [line(pid, v2, flavours=[{"id": fl["id"], "qty": 2}])]}).status_code == 422
    assert c.get("/api/admin/waffle-toppings").status_code in (401, 403)