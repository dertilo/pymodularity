# N1: a sibling file of invoice.py importing its file-private name --
# finding.
from catalog.privacy.receipts.invoice import _fmt


def use():
    return _fmt(1.0)
