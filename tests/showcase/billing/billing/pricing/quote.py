# C4: a cycle that closes through an import inside a function body --
# finding.
from billing.pricing.discount import apply


def quote_price(base: float) -> float:
    return apply(base)
