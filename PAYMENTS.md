# Cravy payments (COD + Easypaisa 03143404938)

## Frontend flow
1. `GET /api/payment-methods` -> show **Cash on Delivery** and **Easypaisa** at checkout.
2. `POST /api/cart/quote` -> server-side totals (never trust client prices).
3. `POST /api/orders` (header `Idempotency-Key`, `payment_method`: `COD` | `EASYPAISA`).
   - COD: `payment_status = UNPAID`, nothing more to do.
   - EASYPAISA: `payment_status = AWAITING_PAYMENT` and the response contains `payment_instructions`
     (`account_number`, `amount`, `reference`, `message`). Show them on the thank-you page.
4. Customer pays in the Easypaisa app, then sends proof (either or both):
   - `POST /api/orders/{order_number}/payment-reference` `{ "transaction_ref": "<TID>", "phone": "<checkout phone>" }`
   - `POST /api/orders/{order_number}/receipt` (multipart: `phone`, `file` = screenshot)
   -> `payment_status = PENDING_VERIFICATION`.
5. `GET /api/orders/{order_number}` to show live status.

## Admin flow
- `PATCH /api/admin/orders/{n}/payment` `{ "status": "PAID" }` after checking the Easypaisa app (or `FAILED` so the customer can resubmit).
- Easypaisa orders cannot move to PREPARING / READY / OUT_FOR_DELIVERY / DELIVERED until payment is `PAID`.
- COD orders become `PAID` automatically when marked `DELIVERED`.
- Cancelling an order gives its coupon use back.

## Rules enforced by the server
- One Transaction ID can only be used for one order (case-insensitive).
- Phone matching ignores formatting (`0300…`, `+92 300…`).
- Proof is refused for cancelled, paid, refunded or COD orders.
- At least one payment method must stay enabled; Easypaisa number must be a valid Pakistani mobile number.
- Number is configurable with the `EASYPAISA_NUMBER` env var (default 03143404938).
