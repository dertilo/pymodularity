# A4: `import a`, then `a._internal.x.f()` -- finding on the module
# `a._internal` (catalog.privacy.attrs_demo._internal), reached by
# attribute chain, from outside attrs_demo/.
import catalog.privacy.attrs_demo as ad


def use():
    return ad._internal.x.f()
