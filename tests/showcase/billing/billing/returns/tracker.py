# C2: the other half of the directory-level cycle -- imports
# shipping.label, a different file than the one shipping imports, so the
# cycle closes only at directory level, never at file level.
from billing.shipping.label import label_text


def track():
    return label_text()
