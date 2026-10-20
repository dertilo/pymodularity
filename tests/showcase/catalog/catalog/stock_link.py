# C5: a cycle across 2 package roots (catalog <-> warehouse).
from warehouse.inventory import levels


def info() -> str:
    return f"stock: {levels()}"
