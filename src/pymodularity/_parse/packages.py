"""Package-root discovery: directories whose pyproject.toml has a
[project] table, used by `_check` (the privacy rule's package-root anchor)
and `_deps` (the dependency-declaration rule's per-package scan unit)."""

import os
import tomllib
from pathlib import Path

from pymodularity._parse.graph import should_skip_dir


def _has_project_table(pyproject: Path) -> bool:
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return "project" in data


def find_package_roots(repo: Path) -> tuple[Path, ...]:
    """Directories (relative to repo) whose pyproject.toml has a [project]
    table, used only to anchor the privacy rule's "package root". Sorted
    deepest-first so the closest ancestor is found first."""
    roots: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        if "pyproject.toml" in filenames and _has_project_table(
            Path(dirpath) / "pyproject.toml"
        ):
            roots.append(Path(dirpath).relative_to(repo))
    return tuple(sorted(roots, key=lambda p: len(p.parts), reverse=True))
