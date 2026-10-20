# G3: a parameter named `mod` anywhere in the file drops the import's
# binding, so a real module-level `mod._x` is missed -- missed.
from catalog.gaps import mod_g2 as mod


def use_other(mod):
    return mod._x


def real_use():  # G3
    return mod._x
