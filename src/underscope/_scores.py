"""Reach (transitive dependents/dependencies) and the file/directory
scores derived from it: `compute_scores`, the shared first step `graph`,
`metrics` and the view summary all build on."""

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path


def _reachable_set(start: Path, adj: Mapping[Path, AbstractSet[Path]]) -> frozenset[Path]:
    """Every other node `start` can reach, following `adj` (BFS/DFS, order
    doesn't matter); `start` itself never counts even inside a cycle back
    to itself."""
    seen: set[Path] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        for nxt in adj.get(node, ()):
            if nxt != start and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return frozenset(seen)


@dataclass(frozen=True, slots=True)
class _Reach:
    """Per file: the set of dependents (other files with an import path to
    it) and the set of dependencies (other files it has an import path
    to), both transitive."""

    dependents: Mapping[Path, frozenset[Path]]
    dependencies: Mapping[Path, frozenset[Path]]


def _compute_reach(
    nodes: AbstractSet[Path],
    adj: Mapping[Path, AbstractSet[Path]],
    radj: Mapping[Path, AbstractSet[Path]],
) -> _Reach:
    dependents = {n: _reachable_set(n, radj) for n in nodes}
    dependencies = {n: _reachable_set(n, adj) for n in nodes}
    return _Reach(dependents, dependencies)


@dataclass(frozen=True, slots=True)
class FileScore:
    """A file's score: dependents x dependencies, both transitive."""

    path: Path
    dependents: int
    dependencies: int
    score: int


@dataclass(frozen=True, slots=True)
class DirScore:
    """A directory's score, measured across its own boundary, repo-wide:
    `reach_in` is how many files outside the directory reach some file
    inside it (transitively); `reach_out` is how many files outside it
    are reached by some file inside it (transitively); score = in x out."""

    path: Path
    reach_in: int
    reach_out: int
    score: int


def _compute_file_scores(
    nodes: AbstractSet[Path],
    dependents: Mapping[Path, AbstractSet[Path]],
    dependencies: Mapping[Path, AbstractSet[Path]],
) -> Mapping[Path, FileScore]:
    scores: dict[Path, FileScore] = {}
    for n in nodes:
        d_in = len(dependents[n])
        d_out = len(dependencies[n])
        scores[n] = FileScore(n, d_in, d_out, d_in * d_out)
    return scores


@dataclass(frozen=True, slots=True)
class Scores:
    """`graph` and `metrics`'s shared first step's result: file and
    directory scores, computed from reach."""

    file_scores: Mapping[Path, FileScore]
    dir_scores: Mapping[Path, DirScore]


def compute_scores(
    dirs_with_py: AbstractSet[Path],
    adj: Mapping[Path, AbstractSet[Path]],
    radj: Mapping[Path, AbstractSet[Path]],
) -> Scores:
    """`graph` and `metrics`'s shared first step: reach, then file and
    directory scores from it."""
    nodes = set(adj)
    reach = _compute_reach(nodes, adj, radj)
    file_scores = _compute_file_scores(nodes, reach.dependents, reach.dependencies)
    dir_scores = _compute_dir_scores(dirs_with_py, reach.dependents, reach.dependencies)
    return Scores(file_scores, dir_scores)


def ancestor_dirs(rel: Path) -> tuple[Path, ...]:
    """The directory holding `rel` and every ancestor directory up to the
    repo root ('.'), inclusive."""
    out = [rel.parent]
    while out[-1] != Path("."):
        out.append(out[-1].parent)
    return tuple(out)


def _compute_dir_scores(
    dirs_with_py: AbstractSet[Path],
    dependents: Mapping[Path, AbstractSet[Path]],
    dependencies: Mapping[Path, AbstractSet[Path]],
) -> Mapping[Path, DirScore]:
    dir_in: dict[Path, set[Path]] = {}
    dir_out: dict[Path, set[Path]] = {}
    for f, dep_set in dependents.items():
        for d in ancestor_dirs(f):
            if d in dirs_with_py:
                dir_in.setdefault(d, set()).update(dep_set)
    for f, dep_set in dependencies.items():
        for d in ancestor_dirs(f):
            if d in dirs_with_py:
                dir_out.setdefault(d, set()).update(dep_set)

    scores: dict[Path, DirScore] = {}
    for d in dirs_with_py:
        in_count = sum(1 for g in dir_in.get(d, set()) if not g.is_relative_to(d))
        out_count = sum(1 for g in dir_out.get(d, set()) if not g.is_relative_to(d))
        scores[d] = DirScore(d, in_count, out_count, in_count * out_count)
    return scores
