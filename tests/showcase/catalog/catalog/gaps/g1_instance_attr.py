# G1: `obj._y` on an instance needs type information this tool doesn't
# have (it tracks module/name bindings, not instance types); missed.
from catalog.gaps.box import Box


def peek(b: Box) -> str:
    return b._y
