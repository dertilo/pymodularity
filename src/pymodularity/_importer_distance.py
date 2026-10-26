"""Importer distance: up-steps a file's importers climb to reach it
today versus the fewest up-steps any directory in its own distribution
(the nearest ancestor with a pyproject.toml, repo root otherwise) would
need. Split out of `_coupling_metrics` to keep that module under the
line-count soft cap."""

from collections import Counter
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from functools import cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class _TopDir:
    """A directory with a count against it (importers from it)."""

    path: Path
    count: int


def _up_steps(a: Path, b: Path) -> int:
    """Levels `a` climbs out of itself to reach a common ancestor with
    `b`. Down-steps are free: a parent directory reaching into its own
    subdirectory costs 0."""
    common = 0
    for x, y in zip(a.parts, b.parts):
        if x != y:
            break
        common += 1
    return len(a.parts) - common


@dataclass(frozen=True, slots=True)
class _ImporterDistance:
    """A file's importer distance: the up-steps its importers climb to
    reach it today versus the fewest up-steps any directory in its own
    distribution would need."""

    path: Path
    importers: int
    now: int
    best_dir: Path
    best: int
    gain: int
    rel: float
    top_importer_dirs: tuple[_TopDir, ...]


def compute_importer_distance(
    nodes: AbstractSet[Path],
    radj: Mapping[Path, AbstractSet[Path]],
    package_roots: Sequence[Path],
) -> tuple[_ImporterDistance, ...]:
    roots = tuple(package_roots)

    @cache
    def dist_root(d: Path) -> Path:
        for r in roots:
            if d == r or d.is_relative_to(r):
                return r
        return Path(".")

    candidate_dirs: set[Path] = set()
    for f in nodes:
        candidate_dirs.add(f.parent)
        candidate_dirs.update(f.parent.parents)

    results: list[_ImporterDistance] = []
    for f in nodes:
        importers = radj.get(f, set()) - {f}
        if not importers:
            continue
        importer_dirs = tuple(p.parent for p in importers)
        root = dist_root(f.parent)

        def cost(d: Path, importer_dirs: tuple[Path, ...] = importer_dirs) -> int:
            return sum(_up_steps(d2, d) for d2 in importer_dirs)

        now = cost(f.parent)
        if now == 0:
            continue
        best_dir, best = f.parent, now
        for d in candidate_dirs:
            if dist_root(d) != root:
                continue
            s = cost(d)
            if s < best:
                best_dir, best = d, s
        gain = now - best
        if gain <= 0:
            continue
        rel = gain / now
        top = Counter(importer_dirs).most_common(3)
        top_dirs = tuple(_TopDir(d, c) for d, c in top)
        results.append(
            _ImporterDistance(f, len(importers), now, best_dir, best, gain, rel, top_dirs)
        )
    return tuple(results)
