"""The real Cravy menu (prices in PKR). Source: app/menu_catalog.json, the product file supplied by the owner.
To change the menu, replace that JSON file and redeploy: the new version is applied once on startup (see app/menu_seed.py).

Final price of a size = product price + that size's additional price (e.g. Mango DOUBLE = 139 + 80 = 219).
Photos: put real WebP URLs in a product's "images" list (or "image") in the JSON, or upload them from the admin panel.
The "image" paths in the supplied file ("products/mango.jpg") are placeholders and are ignored."""
import hashlib
import json
from pathlib import Path

CATALOG_PATH = Path(__file__).with_name("menu_catalog.json")
_raw = CATALOG_PATH.read_bytes()
_data = json.loads(_raw)

CATALOG_SHA = hashlib.sha256(_raw).hexdigest()[:16]  # changes whenever the JSON file changes

CATEGORIES = [(c["id"], c["name"]) for c in _data["categories"]]  # (slug, name), in menu order


def _sizes(p: dict) -> list[tuple[str, int]]:
    """Required option group (Size) -> sizes as (label, additional_price)."""
    for o in p.get("options") or []:
        if o.get("required", True):
            return [(v["label"], int(v.get("additional_price", 0))) for v in o["values"]]
    return []


def _toppings(p: dict) -> list[tuple[str, int]]:
    """Optional option groups -> add-ons as (label, additional_price)."""
    return [(v["label"], int(v.get("additional_price", 0))) for o in (p.get("options") or []) if not o.get("required", True) for v in o["values"]]


def _real_images(p: dict) -> list[str]:
    """Only real, already-uploaded WebP addresses count. Placeholder paths like products/mango.jpg are skipped."""
    raw = p.get("images") or ([p["image"]] if p.get("image") else [])
    return [u for u in raw if isinstance(u, str) and u.split("?")[0].lower().endswith(".webp") and u.startswith(("/media/", "http://", "https://"))]


_ordered = sorted(_data["products"], key=lambda p: (p["category"], p.get("sort_order", 0)))
_cat_order = {slug: i for i, (slug, _) in enumerate(CATEGORIES)}
_ordered.sort(key=lambda p: _cat_order.get(p["category"], 999))

# (slug, name, category slug, description, price, sizes as (label, additional_price) or [], featured)
PRODUCTS = [(p["slug"], p["name"], p["category"], p.get("description"), int(p["price"]), _sizes(p), bool(p.get("is_featured"))) for p in _ordered]
EXTRAS = {p["slug"]: {"addons": _toppings(p), "available": bool(p.get("is_available", True)), "order": int(p.get("sort_order", 0)), "images": _real_images(p)} for p in _ordered}

# Slugs of the previous menu. Used once, to remove those old products when the new menu is applied.
LEGACY_SLUGS = {
    "mango", "blue-berry", "white-vanilla-premium", "caramel-crunch", "cheese-cake", "chocolate-chips", "pista-nut", "king-kulfa",
    "tutti-frutti", "chocolate-fudge-brownie", "strawberry-ripple", "oreo", "pine-apple",
    "apple-mint-margarita", "lemon-margarita", "mint-margarita",
    "blue-lagoon", "sunrise", "strawberry-mojito", "golden-beach", "fruit-punch",
    "carrot-juice", "abc-juice", "apple-juice", "pina-colada", "blue-colada",
    "blue-berry-soda", "lemon-soda", "peach-soda", "apple-soda", "boster-power",
    "banana-shake", "cookie-shake", "chocolate-shake", "strawberry-shake", "cold-coffee", "vanilla-shake",
    "banana-dates-shake", "banana-milk-shake",
}
