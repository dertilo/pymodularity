# A5: attribute form reaching a package-level private name from outside
# the owning directory -- finding.
import catalog.privacy.discounts as pkg2


def use():
    return pkg2._x
