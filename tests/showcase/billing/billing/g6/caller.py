# G6: two files both match this bare import equally well -- no edge, only
# a `warning: ambiguous import` on stderr.
import helpers


def use():
    return helpers
