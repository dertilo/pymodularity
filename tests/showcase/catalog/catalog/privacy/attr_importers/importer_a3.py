# A3: `from a.b import mod`, then `mod._x` -- finding.
from catalog.privacy.attrs_demo.sub import mod


def use():
    return mod._x
