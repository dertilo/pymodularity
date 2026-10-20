# C3: a cycle that closes through an import under `if TYPE_CHECKING:` --
# finding.
from billing.invoicing.ledger import compute_total


def render_total():
    return compute_total()
