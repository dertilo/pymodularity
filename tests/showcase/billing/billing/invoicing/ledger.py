# C3: closes the cycle with statement.py, but only under TYPE_CHECKING.
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from billing.invoicing.statement import render_total


def compute_total() -> float:
    return 0.0


def describe(renderer: "render_total") -> str:  # noqa: F821
    return str(renderer)
