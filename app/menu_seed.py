"""Loads the real Cravy menu (app/menu_data.py) into the database.

* Insert-only: a category or product that already exists is never changed, so the admin's edits
  (discounts, images, prices, availability) are safe, and re-running does nothing.
* The placeholder "Demo ..." products from the starter seed are removed once the real menu is in
  (past orders keep their own name/price snapshots, so no order is affected).
* On startup it runs only if none of the real products exist yet (see ensure_menu).
* When the menu file (app/menu_catalog.json) is replaced with a new version, sync_menu() applies it ONCE on the next
  start: products are matched by slug and updated (name, text, category, price, sizes, order, featured, available),
  new ones are added, and the products of the previous menu that are no longer in the file are removed. The admin's own
  discounts and uploaded photos are kept.
Run by hand:  python -m app.menu_seed            (insert-only, safe to repeat)
              python -m app.menu_seed --sync     (apply the menu file now: update, add and remove)
"""
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models
from .db import SessionLocal
from .menu_data import CATALOG_SHA, CATEGORIES, EXTRAS, LEGACY_SLUGS, PRODUCTS

log = logging.getLogger(__name__)


META_KEY = "menu_catalog_sha"


def _get_meta(db: Session) -> str | None:
    row = db.get(models.AppMeta, META_KEY)
    return row.value if row else None


def _set_meta(db: Session, value: str) -> None:
    row = db.get(models.AppMeta, META_KEY)
    if row is None:
        db.add(models.AppMeta(key=META_KEY, value=value))
    else:
        row.value = value


def _variants(price: int, sizes) -> list[models.Variant]:
    return [models.Variant(name=label, price=price + extra, sort_order=k) for k, (label, extra) in enumerate(sizes)]


def _addons(slug: str) -> list[models.Addon]:
    return [models.Addon(name=label, price=extra, sort_order=k) for k, (label, extra) in enumerate(EXTRAS[slug]["addons"])]


def _new_product(slug, name, category_id, description, price, sizes, featured, order) -> models.Product:
    p = models.Product(
        slug=slug, name=name, category_id=category_id, description=description, base_price=price,
        is_available=EXTRAS[slug]["available"], is_featured=featured, is_bestseller=False, is_demo=False, sort_order=order,
        variants=_variants(price, sizes), addons=_addons(slug),
    )
    if EXTRAS[slug]["images"]:
        p.set_images(EXTRAS[slug]["images"])
    return p


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
        db.add(_new_product(slug, name, cats[cat_slug].id, description, price, sizes, featured, n))
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
    if added_products:
        _set_meta(db, CATALOG_SHA)  # this version of the menu file is now in
    db.commit()
    return {"categories_added": added_cats, "products_added": added_products, "demo_removed": removed}


def sync_menu(db: Session) -> dict:
    """Make the database match the menu file. Products are matched by slug.
    Updated: name, description, category, price, sizes, toppings, order, featured, available (and photos the file lists).
    Kept as they are: the admin's discount and the photos uploaded from the admin panel.
    Removed: starter "Demo ..." products and the products of the previous menu that the file no longer has."""
    cats: dict[str, models.Category] = {}
    for i, (slug, name) in enumerate(CATEGORIES):
        c = db.scalar(select(models.Category).where(models.Category.slug == slug))
        if c is None:
            c = models.Category(slug=slug, name=name, sort_order=i, is_active=True)
            db.add(c)
        c.name, c.sort_order, c.is_active = name, i, True
        cats[slug] = c
    db.flush()
    updated = added = 0
    per_cat: dict[str, int] = {}
    keep = set()
    for slug, name, cat_slug, description, price, sizes, featured in PRODUCTS:
        keep.add(slug)
        order = per_cat[cat_slug] = per_cat.get(cat_slug, 0) + 1
        p = db.scalar(select(models.Product).where(models.Product.slug == slug))
        if p is None:
            db.add(_new_product(slug, name, cats[cat_slug].id, description, price, sizes, featured, order))
            added += 1
            continue
        p.name, p.description, p.category_id, p.base_price = name, description, cats[cat_slug].id, price
        p.is_featured, p.is_available, p.sort_order, p.is_demo = featured, EXTRAS[slug]["available"], order, False
        p.variants = _variants(price, sizes)
        p.addons = _addons(slug)
        if EXTRAS[slug]["images"]:
            p.set_images(EXTRAS[slug]["images"])
        updated += 1
    db.flush()
    removed = 0
    for p in db.scalars(select(models.Product)).all():
        if p.slug not in keep and (p.is_demo or p.slug in LEGACY_SLUGS):
            db.delete(p)  # past orders keep their own name/price snapshots
            removed += 1
    db.flush()
    for c in db.scalars(select(models.Category)).all():  # starter categories left empty by the clean-up
        if c.slug not in cats and not c.products:
            db.delete(c)
    _set_meta(db, CATALOG_SHA)
    db.commit()
    return {"products_updated": updated, "products_added": added, "products_removed": removed}


def ensure_menu(db: Session) -> None:
    """Startup hook.
    * No real product yet: import the whole menu.
    * Menu present but from an older version of the menu file: apply the new file once (sync_menu).
    * Otherwise nothing happens, so a product the admin deleted or edited later is never brought back or overwritten."""
    slugs = [p[0] for p in PRODUCTS]
    if db.scalar(select(models.Product.id).where(models.Product.slug.in_(slugs)).limit(1)) is None and db.scalar(
        select(models.Product.id).where(models.Product.slug.in_(LEGACY_SLUGS)).limit(1)
    ) is None:
        log.info("Menu import: %s", import_menu(db))
    elif _get_meta(db) != CATALOG_SHA:
        log.info("Menu update from menu_catalog.json: %s", sync_menu(db))


if __name__ == "__main__":
    import sys

    with SessionLocal() as session:
        print(sync_menu(session) if "--sync" in sys.argv else import_menu(session))
