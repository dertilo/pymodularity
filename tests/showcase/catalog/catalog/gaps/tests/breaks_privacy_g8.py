# G8: a violation inside a directory literally named "tests" -- the whole
# directory is skipped by the tool (and by pytest, since this file isn't
# named test_*.py/*_test.py), so this would-be finding never surfaces.
from catalog.privacy.pricing._pricing import compute


def use():
    return compute(1.0)
