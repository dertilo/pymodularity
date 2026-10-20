# C5: a cycle across 2 package roots (catalog <-> warehouse).
from catalog.stock_link import info


def levels() -> int:
    return 42 if info else 0
