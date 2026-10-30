def apply_discount(price: int, percent: int | None) -> int:
    """Price after a product's percentage discount, in whole PKR (rounded half up). 0 % or None = unchanged."""
    pct = max(0, min(int(percent or 0), 100))
    return (price * (100 - pct) + 50) // 100
