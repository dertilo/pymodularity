# C4: closes the cycle with quote.py, but only inside a function body.


def apply(base: float) -> float:
    from billing.pricing.quote import quote_price

    return base * 0.95 if quote_price else base
