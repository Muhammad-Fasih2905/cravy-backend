"""Printable order slip (80 mm thermal printer friendly, also fine on A4). All customer text is HTML-escaped."""
from datetime import timedelta, timezone
from html import escape

PKT = timezone(timedelta(hours=5))
PAY_LABEL = {"COD": "Cash on Delivery", "EASYPAISA": "Easypaisa"}


def _rs(n: int) -> str:
    return f"Rs. {n:,}"


def render_slip(d: dict, business_name: str, phone: str | None, autoprint: bool) -> str:
    from datetime import datetime
    e = escape
    placed = datetime.fromisoformat(d["created_at"]).astimezone(PKT).strftime("%d %b %Y, %I:%M %p")
    rows = []
    for i in d["items"]:
        name = e(i["name"]) + (f" ({e(i['variant'])})" if i["variant"] else "")
        extras = "".join(f"<div class='sub'>+ {e(a['name'])}</div>" for a in (i["addons"] or []))
        rows.append(f"<tr><td>{i['quantity']} x {name}{extras}</td><td class='r'>{_rs(i['line_total'])}</td></tr>")
    line = lambda k, v: f"<tr><td>{k}</td><td class='r'>{v}</td></tr>"  # noqa: E731
    sums = [line("Subtotal", _rs(d["subtotal"]))]
    if d["discount_total"]:
        sums.append(line(f"Discount{' (' + e(d['coupon_code']) + ')' if d['coupon_code'] else ''}", "- " + _rs(d["discount_total"])))
    if d["tax_total"]:
        sums.append(line("Tax", _rs(d["tax_total"])))
    band = f" ({e(d['delivery_zone'])})" if d.get("delivery_zone") else ""
    sums.append(line(f"Delivery{band}", "FREE" if d["delivery_charge"] == 0 else _rs(d["delivery_charge"])))
    method = PAY_LABEL.get(d["payment_method"], e(d["payment_method"]))
    if d["payment_method"] == "COD":
        pay = f"COLLECT {_rs(d['total'])} IN CASH" if d["status"] != "COMPLETED" else f"PAID IN CASH {_rs(d['total'])}"
    else:
        pay = "PAID" if d["payment_status"] == "PAID" else f"Payment: {e(d['payment_status'].replace('_', ' ').title())}"
    where = ", ".join(x for x in (e(d["address"]), e(d["city"])) if x)
    extra = ""
    if d.get("landmark"):
        extra += f"<div>Landmark: {e(d['landmark'])}</div>"
    if d.get("distance_km") is not None:
        extra += f"<div>Distance: {d['distance_km']:.1f} km</div>"
    if d.get("instructions"):
        extra += f"<div class='note'>Note: {e(d['instructions'])}</div>"
    script = "<script>window.onload=function(){window.print()}</script>" if autoprint else ""
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Order {e(d['order_number'])}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@page {{ size: 80mm auto; margin: 3mm; }}
body {{ font: 13px/1.35 'Courier New', monospace; width: 74mm; max-width: 100%; margin: 0 auto; padding: 6px; color: #000; background: #fff; }}
h1 {{ font-size: 18px; margin: 0; text-align: center; }} .c {{ text-align: center; }} hr {{ border: 0; border-top: 1px dashed #000; margin: 6px 0; }}
table {{ width: 100%; border-collapse: collapse; }} td {{ vertical-align: top; padding: 1px 0; }} .r {{ text-align: right; white-space: nowrap; padding-left: 6px; }}
.sub {{ font-size: 11px; padding-left: 10px; }} .big {{ font-size: 16px; font-weight: bold; }} .note {{ font-weight: bold; }}
.pay {{ border: 2px solid #000; text-align: center; padding: 4px; font-weight: bold; font-size: 15px; margin-top: 6px; }}
</style></head><body>
<h1>{e(business_name)}</h1>{f"<div class='c'>{e(phone)}</div>" if phone else ""}
<hr><div class="c big">{e(d['order_number'])}</div><div class="c">{placed}</div>
<hr><div><b>{e(d['customer_name'])}</b></div><div>{e(d['customer_phone'])}</div><div>{where}</div>{extra}
<hr><table>{''.join(rows)}</table><hr><table>{''.join(sums)}
<tr class="big"><td>TOTAL</td><td class="r">{_rs(d['total'])}</td></tr></table>
<div>Payment: {method}</div><div class="pay">{pay}</div>
<hr><div class="c">Thank you for ordering!</div>{script}</body></html>"""
