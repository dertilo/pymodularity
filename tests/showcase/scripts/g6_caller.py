"""G6: `billing/` and `catalog/` each expose a top-level `g6app.main` module.

This script sits under neither package root, so no ancestor root wins --
the import stays genuinely ambiguous even under the new root-based rule.
Only a `warning: ambiguous import` is produced on stderr; no edge is drawn.
"""

import g6app.main


def use():
    return g6app.main
