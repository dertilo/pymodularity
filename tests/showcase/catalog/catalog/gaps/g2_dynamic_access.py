# G2: dynamic access bypasses the static import graph entirely; missed.
import importlib

from catalog.gaps import mod_g2


def use():
    private_value = getattr(mod_g2, "_x")
    tax = importlib.import_module("catalog.gaps.sealed._internal_g2.tax")
    return private_value, tax
