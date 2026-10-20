# M3: the parent directory of pricing/ importing its private module --
# finding.
from catalog.privacy.pricing import _pricing

# N3: the same file importing a private name from a package __init__.py,
# from outside that package -- finding.
from catalog.privacy.discounts import _x


def use():
    return _pricing, _x
