"""N1 target."""


def _fmt(amount: float) -> str:
    return f"{amount:.2f}"


def render(amount: float) -> str:
    return _fmt(amount)
