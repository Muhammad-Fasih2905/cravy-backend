"""Fixed delivery rates by distance from the shop. These are permanent and apply to every order.

    up to 1 km  FREE   | 5-6 km  Rs. 219
    1-2 km  Rs. 79     | 6-7 km  Rs. 239
    2-3 km  Rs. 99     | 7-8 km  Rs. 249
    3-4 km  Rs. 149    | 8-9 km  Rs. 269
    4-5 km  Rs. 199    | 9-10 km Rs. 299      (beyond 10 km: we do not deliver)

A distance exactly on a boundary belongs to the cheaper band (1.00 km is free, 2.00 km is Rs. 79).
Distance is measured server-side from the customer's map pin to the shop; the client never sends a price or a distance.
"""
import math

MAX_KM = 10
# (upper limit in km, charge in PKR)
RATES: list[tuple[int, int]] = [(1, 0), (2, 79), (3, 99), (4, 149), (5, 199), (6, 219), (7, 239), (8, 249), (9, 269), (10, 299)]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def charge_for(km: float) -> int | None:
    """PKR charge for a distance, or None when it is beyond the delivery range."""
    km = round(km, 2)
    for limit, charge in RATES:
        if km <= limit:
            return charge
    return None


def band_label(km: float) -> str | None:
    km = round(km, 2)
    lower = 0
    for limit, _ in RATES:
        if km <= limit:
            return "Up to 1 km" if limit == 1 else f"{lower}-{limit} km"
        lower = limit
    return None


def rates_table() -> list[dict]:
    out, lower = [], 0
    for limit, charge in RATES:
        out.append({"label": "Up to 1 km" if limit == 1 else f"{lower}-{limit} km", "from_km": lower, "to_km": limit,
                    "charge": charge, "free": charge == 0})
        lower = limit
    return out
