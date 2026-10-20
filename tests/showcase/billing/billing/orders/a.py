# C1: two files that import each other directly -- finding.
from billing.orders.b import make_b


def make_a():
    return "a", make_b
