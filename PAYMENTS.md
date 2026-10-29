# Cravy: orders, COD, Easypaisa, delivery, live updates

## Order life cycle (simple on purpose)
`PENDING` -> `COMPLETED` (admin, once the payment has come in)   or   `PENDING` -> `CANCELLED`.
There are no preparing / dispatch / delivery steps. COMPLETED and CANCELLED are final.

| Method | Payment status path |
|---|---|
| COD | `UNPAID` -> `PAID`. Marking the order COMPLETED records the cash as PAID in the same save. |
| EASYPAISA (03143404938) | `AWAITING_PAYMENT` -> `PENDING_VERIFICATION` (customer sent TID/screenshot) -> `PAID` (admin) or `FAILED`. The order can only be COMPLETED once payment is `PAID`. |

## Fixed delivery rates (server-side, permanent; see app/delivery.py)
Up to 1 km FREE | 1-2 km Rs.79 | 2-3 km Rs.99 | 3-4 km Rs.149 | 4-5 km Rs.199 | 5-6 km Rs.219 | 6-7 km Rs.239 | 7-8 km Rs.249 | 8-9 km Rs.269 | 9-10 km Rs.299 | over 10 km: not delivered.
A distance exactly on a boundary takes the cheaper band. Distance is straight-line (as the crow flies) from the shop to the customer's map pin, measured by the server.
Shop location (Akber Paradise, Block 10-A Gulshan-e-Iqbal) is built in: 24.911012, 67.105612. Override with `SHOP_LATITUDE` / `SHOP_LONGITUDE` or `PUT /api/admin/settings` (`shop_latitude`, `shop_longitude`) if the shop moves.

## Customer API
- `GET  /api/delivery-rates` -> price list for display.
- `GET  /api/payment-methods` -> COD + Easypaisa.
- `POST /api/cart/quote` `{items, coupon_code?, latitude?, longitude?}` -> totals. Without a location: `delivery_pending=true`. Beyond range: `deliverable=false` + `delivery_message`. Otherwise `delivery_charge`, `distance_km`, `delivery_band`.
- `POST /api/orders` (header `Idempotency-Key`) `{customer_name, customer_phone, city, address, latitude, longitude, payment_method: "COD"|"EASYPAISA", items, ...}`. Location is required; over 10 km -> 422.
- `GET  /api/orders/{order_number}` -> status + payment status (+ `payment_instructions` for Easypaisa).
- Easypaisa proof: `POST /api/orders/{n}/payment-reference` `{transaction_ref, phone}` and/or `POST /api/orders/{n}/receipt` (multipart `phone`, `file`).

## Admin API (Authorization: Bearer <token> from POST /api/admin/login)
- `GET   /api/admin/orders?status=PENDING|COMPLETED|CANCELLED&method=&q=&updated_since=ISO&limit=` -> always fresh from the database (`Cache-Control: no-store`).
- `GET   /api/admin/orders/{n}` -> one order.
- `PATCH /api/admin/orders/{n}/status` `{status: "COMPLETED"|"CANCELLED"}` -> returns the saved order.
- `PATCH /api/admin/orders/{n}/payment` `{status, note?}` (use for Easypaisa verification).
- `GET   /api/admin/orders/{n}/slip?autoprint=1&token=<token>` -> printable 80 mm slip (HTML). Open in a new tab to print.
- `GET   /api/admin/orders/stream?token=<token>` -> live Server-Sent Events: `order.created`, `order.updated` (each carries the full saved order, same shape as the list). Heartbeat every 15 s.
- `GET   /api/admin/stats` -> `pending_orders`, `completed_orders`, `total_sales` (completed only), `pending_amount`, ...

## Why the dashboard can no longer "jump" after a refresh
Every change is committed to the database first, then pushed live, and the PATCH response is the saved order. The list endpoints are never cached.
The frontend must therefore: (1) render from the PATCH response / live event (not from a guessed local state), (2) on page load fetch `GET /api/admin/orders`, (3) after a dropped stream reconnect and call `GET /api/admin/orders?updated_since=<last event time>`.
Note: the live feed is in-memory, so run a single server process (the Procfile does). For several processes it needs Redis pub/sub.
