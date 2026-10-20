# A6: `from a.b.mod import _x` plus `a.b.mod._x` in the same file --
# exactly 1 finding (the attribute form is deduplicated against the import
# form reporting the same importer/target/name).
from catalog.privacy.attrs_demo.sub.mod import _x
import catalog.privacy.attrs_demo.sub.mod


def use():
    return _x, catalog.privacy.attrs_demo.sub.mod._x
