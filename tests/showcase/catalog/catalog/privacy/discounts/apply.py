# N2: imported under the owning directory -- allowed.
from catalog.privacy.discounts import _x

# A5: attribute form, same directory -- allowed.
import catalog.privacy.discounts as pkg


def discount(price: float) -> float:
    return price * (1 - _x) * (1 - pkg._x)
