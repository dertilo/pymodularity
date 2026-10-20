# G4: a base name bound to an unresolvable/third-party module; no repo
# file or folder is named `numpy`, so the import never resolves inside
# the repo and the private access escapes detection; missed.
import numpy


def use():
    return numpy._x
