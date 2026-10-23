"""Demo data. EVERYTHING here is placeholder: replace names/prices with the real Cravy menu.
Categories were read from the supplied menu image (item names/prices were not legible).
Run: python -m app.seed   (idempotent)"""
from sqlalchemy import select

from . import models
from .config import settings
from .db import Base, SessionLocal, engine
from .security import hash_password

S = lambda a, b, c: [("Small", a), ("Regular", b), ("Large", c)]  # noqa: E731

MENU = [
    ("Ice Creams", "ice-creams", [
        ("Demo Vanilla Scoop", "demo-vanilla-scoop", 200, [("Single scoop", 200), ("Double scoop", 350)],
         [("Chocolate sauce", 40), ("Sprinkles", 30)], True, True),
        ("Demo Strawberry Cup", "demo-strawberry-cup", 250, [], [("Extra topping", 50)], False, False),
    ]),
    ("Coldies", "coldies", [("Demo Cold Drink", "demo-cold-drink", 300, S(250, 300, 380), [], True, True)]),
    ("Margaritas", "margaritas", [("Demo Margarita", "demo-margarita", 350, S(300, 350, 450), [], False, False)]),
    ("Mocktails", "mocktails", [("Demo Mocktail", "demo-mocktail", 380, S(320, 380, 480), [], False, True)]),
    ("Ice Cream Cones", "ice-cream-cones", [("Demo Cone", "demo-cone", 150, [], [], False, False)]),
]


def run() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        if db.get(models.BusinessSettings, 1) is None:
            db.add(models.BusinessSettings(id=1, announcement="Demo announcement: edit in settings.", delivery_charge=100, min_order_value=300))
        for ci, (cname, cslug, prods) in enumerate(MENU):
            cat = db.scalar(select(models.Category).where(models.Category.slug == cslug)) or models.Category(slug=cslug, name=cname)
            cat.name, cat.sort_order = cname, ci
            db.add(cat)
            db.flush()
            for pi, (name, slug, base, variants, addons, best, feat) in enumerate(prods):
                p = db.scalar(select(models.Product).where(models.Product.slug == slug)) or models.Product(slug=slug, category_id=cat.id)
                p.name, p.category_id, p.base_price, p.sort_order = name, cat.id, base, pi
                p.description = "Demo item. Replace with a real Cravy product."
                p.is_bestseller, p.is_featured, p.is_demo, p.prep_minutes = best, feat, True, 5
                p.variants = [models.Variant(name=n, price=pr, sort_order=i) for i, (n, pr) in enumerate(variants)]
                p.addons = [models.Addon(name=n, price=pr, sort_order=i) for i, (n, pr) in enumerate(addons)]
                db.add(p)
        if db.scalar(select(models.Coupon).where(models.Coupon.code == "DEMO10")) is None:
            db.add(models.Coupon(code="DEMO10", type="PERCENT", value=10, max_discount=200))
        if settings.admin_email and settings.admin_password and db.scalar(select(models.AdminUser)) is None:
            db.add(models.AdminUser(email=settings.admin_email.strip().lower(), password_hash=hash_password(settings.admin_password)))
        db.commit()
    print("Seeded demo menu (all products flagged is_demo).")


if __name__ == "__main__":
    run()
