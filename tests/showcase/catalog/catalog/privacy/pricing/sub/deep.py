# M1: a subdirectory of _pricing.py's own directory -- allowed.
from catalog.privacy.pricing._pricing import compute


def deep_price(base: float) -> float:
    return compute(base)
