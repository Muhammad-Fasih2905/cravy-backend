"""Loads the real Cravy menu (app/menu_data.py) into the database.

* Insert-only: a category or product that already exists is never changed, so the admin's edits
  (discounts, images, prices, availability) are safe, and re-running does nothing.
* The placeholder "Demo ..." products from the starter seed are removed once the real menu is in
  (past orders keep their own name/price snapshots, so no order is affected).
* On startup it runs only if none of the real products exist yet (see ensure_menu).
Run by hand:  python -m app.menu_seed
"""
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models
from .db import SessionLocal
from .menu_data import CATEGORIES, PRODUCTS

log = logging.getLogger(__name__)


def import_menu(db: Session) -> dict:
    cats: dict[str, models.Category] = {}
    added_cats = added_products = 0
    for i, (slug, name) in enumerate(CATEGORIES):
        c = db.scalar(select(models.Category).where(models.Category.slug == slug))
        if c is None:
            c = models.Category(slug=slug, name=name, sort_order=i, is_active=True)
            db.add(c)
            added_cats += 1
        elif any(p.is_demo for p in c.products):  # a starter category (e.g. "Margaritas") reused for the real menu
            c.name, c.sort_order, c.is_active = name, i, True
        cats[slug] = c
    db.flush()
    per_cat: dict[str, int] = {}
    for slug, name, cat_slug, description, price, sizes, featured in PRODUCTS:
        n = per_cat[cat_slug] = per_cat.get(cat_slug, 0) + 1
        if db.scalar(select(models.Product.id).where(models.Product.slug == slug)):
            continue
        db.add(models.Product(
            slug=slug, name=name, category_id=cats[cat_slug].id, description=description, base_price=price,
            is_available=True, is_featured=featured, is_bestseller=False, is_demo=False, sort_order=n,
            variants=[models.Variant(name=label, price=price + extra, sort_order=k) for k, (label, extra) in enumerate(sizes)],
        ))
        added_products += 1
    db.flush()
    removed = 0
    if db.scalar(select(func.count(models.Product.id)).where(models.Product.is_demo.is_(False))):
        for p in db.scalars(select(models.Product).where(models.Product.is_demo.is_(True))).all():
            db.delete(p)
            removed += 1
        db.flush()
        for c in db.scalars(select(models.Category)).all():  # starter categories left empty by the clean-up
            if c.slug not in cats and not c.products:
                db.delete(c)
    db.commit()
    return {"categories_added": added_cats, "products_added": added_products, "demo_removed": removed}


def ensure_menu(db: Session) -> None:
    """Startup hook. Imports the menu only when none of the real products exist yet, so deleting or editing a
    product later is never undone."""
    slugs = [p[0] for p in PRODUCTS]
    if db.scalar(select(models.Product.id).where(models.Product.slug.in_(slugs)).limit(1)) is None:
        log.info("Menu import: %s", import_menu(db))


if __name__ == "__main__":
    with SessionLocal() as session:
        print(import_menu(session))
