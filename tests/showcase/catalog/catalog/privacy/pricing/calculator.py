# M1: same directory as _pricing.py -- allowed.
from catalog.privacy.pricing._pricing import compute


def price(base: float) -> float:
    return compute(base)
