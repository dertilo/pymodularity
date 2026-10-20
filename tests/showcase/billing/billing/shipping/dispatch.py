# C2: a cycle between 2 directories (shipping <-> returns) with no
# file-level cycle -- dispatch imports returns.refund, a different file
# than the one that imports back, so no single file sits in a loop.
from billing.returns.refund import refund_amount


def dispatch():
    return refund_amount()
