# M4: the same private module (catalog's _pricing.py) imported from
# another package root entirely -- finding.
from catalog.privacy.pricing._pricing import compute


def reprice(base: float) -> float:
    return compute(base)
