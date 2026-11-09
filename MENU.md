# Menu, discounts and product images

## Menu
The real Cravy menu lives in `app/menu_catalog.json` (8 categories, 39 products, prices in PKR); `app/menu_data.py` reads it.
Size price = product price + that size's `additional_price` (Mango DOUBLE = 139 + 80 = 219).

**Changing the menu:** replace `app/menu_catalog.json` with the new file and redeploy. On the next start the new version is applied **once**:
products are matched by `slug` and updated (name, description, category, price, sizes, order, featured, available), new ones are added,
and the products of the previous menu that are no longer in the file (and the starter "Demo ..." items) are removed. The admin's own
discounts and the photos uploaded from the admin panel are kept. After that single run nothing is overwritten on restart, so later edits and deletes stay.
Manual runs: `python -m app.menu_seed` (insert-only, safe to repeat) or `python -m app.menu_seed --sync` (apply the file now).
Products the admin added by hand (slugs that were never in the menu file) are never removed.

## Discount (admin decides per product)
`PATCH /api/admin/products/{id}` with `{"discount_percent": 0-90}` (0 removes it).
- `/api/menu` and `/api/products/{slug}` return `discount_percent`, `final_base_price` and, per size, `price` (regular) and `final_price` (what the customer pays).
- Checkout/quote always charges the discounted price, computed on the server. Quote lines carry `discount_percent`.
- The discount applies to the size/base price (not to add-ons) and stacks with a promo code.
- `/api/menu` is cached for up to 30 seconds, so a new discount can take that long to appear.

## Product images (several photos per product)
Every product has up to 8 photo slots. `/api/menu` and `/api/products/{slug}` return `images` (all photos, in slot order) and
`image_url` (always the first photo, kept for older code). Show every photo of `images` (gallery / slider).
All photos are **WebP only** (checked by file content, max 3 MB each); other formats get a 422.
- `POST /api/admin/products/{id}/image` (multipart `file`, optional `?slot=N`, default 0): puts one photo in a slot. An occupied slot is replaced; the slot after the last one adds a photo.
- `POST /api/admin/products/{id}/images` (multipart `files`, several): adds the photos to the next free slots, in the order sent. Nothing is added if one file is not WebP.
- `DELETE /api/admin/products/{id}/image?slot=N`: removes that photo; the later ones move up (so slot 0, the main photo, is always filled while any photo exists).
- `app/menu_catalog.json` may also list ready photos: `"images": ["/media/products/a.webp", ...]` (or `"image"`); only real `.webp` addresses (starting with `/media/` or `http`) are used, the placeholder paths like `products/mango.jpg` are ignored.
Uploaded files are stored in the folder given by `UPLOAD_DIR`. On Railway the disk is wiped on every deploy, so mount a Volume and point `UPLOAD_DIR` at it, otherwise the images disappear after a redeploy.

## Adding a category from the admin panel
`POST /api/admin/categories {"name": "Waffles"}` (admin only) creates the category and returns `{id, name, slug, created}`. The same name (any capitalisation) returns the existing category instead of a duplicate. The admin "Add a new product" form has a "New category" button that uses it and selects the new category for the product. On the menu page a category that is not listed in `config/menuGroups.ts` appears under the "More" heading.

## Menu file on an already-deployed site
When `menu_catalog.json` changes, the next start only ADDS categories/products the database does not have. Nothing that exists is changed or removed, so admin edits (prices, photos, sizes, new categories/products) are safe. To overwrite everything from the file on purpose: `python -m app.menu_seed --sync`.

## Frequently bought together
Each product can list other products (any menu, up to 8) that are suggested under it with their picture and price. `GET/PUT /api/admin/products/{id}/bundle` (admin only; PUT body `{"product_ids":[...]}`, order = display order). The public menu / product output has `bundle: [{id, name, slug, image_url, variant_id, variant_name, price}]` (only products that are available). The customer ticks them and each is added to the cart as its own line, priced again by the server. Loaded Waffle -> Vanilla Cream Waffle is set once at startup; after that only the admin changes it. New table: `product_pairings` (created automatically).
