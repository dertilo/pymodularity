# C1: two files that import each other directly -- finding.
from billing.orders.a import make_a


def make_b():
    return "b", make_a
