# Menu, discounts and product images

## Menu
The real Cravy menu lives in `app/menu_data.py` (8 categories, 39 products, prices in PKR).
Size price = product price + that size's surcharge (Mango DOUBLE = 160 + 115 = 275). Products without sizes use the product price.
It is imported automatically on startup, but only when none of these products exist yet, so a product the admin deleted or edited is never brought back or overwritten. The starter "Demo ..." products are removed at that time (old orders keep their own name/price snapshots).
Manual run: `python -m app.menu_seed` (insert-only, safe to repeat).

## Discount (admin decides per product)
`PATCH /api/admin/products/{id}` with `{"discount_percent": 0-90}` (0 removes it).
- `/api/menu` and `/api/products/{slug}` return `discount_percent`, `final_base_price` and, per size, `price` (regular) and `final_price` (what the customer pays).
- Checkout/quote always charges the discounted price, computed on the server. Quote lines carry `discount_percent`.
- The discount applies to the size/base price (not to add-ons) and stacks with a promo code.
- `/api/menu` is cached for up to 30 seconds, so a new discount can take that long to appear.

## Product images
`POST /api/admin/products/{id}/image` (multipart `file`): **WebP only** (checked by file content, max 3 MB). Other formats get a 422.
Uploaded files are stored in the folder given by `UPLOAD_DIR`. On Railway the disk is wiped on every deploy, so mount a Volume and point `UPLOAD_DIR` at it, otherwise the images disappear after a redeploy.
