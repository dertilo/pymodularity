"""Package-root discovery: directories whose pyproject.toml has a
[project] table, used by `_check` (the privacy rule's package-root anchor)
and `_deps` (the dependency-declaration rule's per-package scan unit).
`should_skip_dir` lives here (not in `graph`) so `graph` can import
`find_package_roots` for import-root resolution without a cycle.

`is_path_excluded` is the one place the user-configured `exclude` patterns
(`--exclude` plus `[tool.pymodularity] exclude` in the repo's own
pyproject.toml, see `load_exclude_config`) are matched; every repo walk
routes its directory/file pruning through it instead of copying the
fnmatch logic per site."""

import fnmatch
import os
import tomllib
from collections.abc import Sequence
from pathlib import Path

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "build",
        "dist",
        "tests",
        "test",
    }
)


def should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIRS or name.startswith(".")


def is_path_excluded(rel_path: Path, excludes: Sequence[str]) -> bool:
    """True if `rel_path` (relative to the repo root) matches any
    user-configured exclude pattern, fnmatch against its POSIX form. A
    directory match prunes the whole walk below it, so callers only need
    to check each path once, not its ancestors too."""
    if not excludes:
        return False
    posix = rel_path.as_posix()
    return any(fnmatch.fnmatch(posix, pattern) for pattern in excludes)


def load_exclude_config(repo: Path) -> tuple[str, ...]:
    """`[tool.pymodularity] exclude` from the repo root's own
    pyproject.toml. A missing file or table means no excludes, same as an
    empty list."""
    pyproject = repo / "pyproject.toml"
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return ()
    tool = data.get("tool", {})
    pymodularity_cfg = tool.get("pymodularity", {}) if isinstance(tool, dict) else {}
    configured = (
        pymodularity_cfg.get("exclude", []) if isinstance(pymodularity_cfg, dict) else []
    )
    if not isinstance(configured, list):
        return ()
    return tuple(str(pattern) for pattern in configured)


def _has_project_table(pyproject: Path) -> bool:
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return "project" in data


def find_package_roots(repo: Path, excludes: Sequence[str] = ()) -> tuple[Path, ...]:
    """Directories (relative to repo) whose pyproject.toml has a [project]
    table, used only to anchor the privacy rule's "package root". Sorted
    deepest-first so the closest ancestor is found first."""
    roots: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        rel_dir = Path(dirpath).relative_to(repo)
        dirnames[:] = [
            d
            for d in dirnames
            if not should_skip_dir(d) and not is_path_excluded(rel_dir / d, excludes)
        ]
        if "pyproject.toml" in filenames and _has_project_table(
            Path(dirpath) / "pyproject.toml"
        ):
            roots.append(rel_dir)
    return tuple(sorted(roots, key=lambda p: len(p.parts), reverse=True))
