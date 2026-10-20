# M2: a sibling directory of pricing/ importing its private module --
# finding.
from catalog.privacy.pricing._pricing import compute


def use():
    return compute(1.0)
