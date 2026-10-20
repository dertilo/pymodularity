"""The module index (`Index`/`build_index`) and import resolution
(`resolve_exact`/`resolve_relative`, returning the `Resolved | Ambiguous |
_Unresolved` sum) that `graph._collect_edges` drives one ast walk with."""

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pymodularity._parse.names import module_comp

# dotted module path components, e.g. ("shop", "billing", "pricing")
_PathParts = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ModuleCandidate:
    """A resolvable module: its file/dir path, and whether it's a namespace
    package directory (True) or a single .py file / __init__.py (False)."""

    path: Path
    is_namespace_dir: bool


ModuleCandidates = Mapping[_PathParts, _ModuleCandidate]


@dataclass(frozen=True, slots=True)
class _SuffixEntry:
    """A module candidate indexed by dotted suffix."""

    comp: _PathParts
    path: Path
    is_namespace_dir: bool


SuffixIndex = Mapping[_PathParts, tuple[_SuffixEntry, ...]]


@dataclass(frozen=True, slots=True)
class Resolved:
    """A single unambiguous module match."""

    path: Path


@dataclass(frozen=True, slots=True)
class Ambiguous:
    """Several module matches equally plausible for the importer; the
    caller records a warning and treats the import as unresolved."""

    candidates: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _Unresolved:
    """No module matched (stdlib, third-party, or a dotted path with no
    candidate in the repo)."""


_Resolution = Resolved | Ambiguous | _Unresolved


@dataclass(frozen=True, slots=True)
class _ScoredCandidate:
    """A module candidate scored for disambiguation against the importer."""

    root_depth: int
    is_ancestor: bool
    path: Path
    root: _PathParts
    is_namespace_dir: bool


@dataclass(frozen=True, slots=True)
class Index:
    """What a repo's file list indexes into: the directories holding Python
    code, every resolvable module candidate, and that candidate set indexed
    by dotted suffix for lookup."""

    dirs_with_py: frozenset[Path]
    module_candidates: ModuleCandidates
    suffix_index: SuffixIndex


def build_index(repo: Path, py_files: Sequence[Path]) -> Index:
    dirs_with_py: set[Path] = set()
    for f in py_files:
        d = f.relative_to(repo).parent
        while True:
            dirs_with_py.add(d)
            if d == Path("."):
                break
            d = d.parent

    module_candidates: dict[_PathParts, _ModuleCandidate] = {}
    for f in py_files:
        rel = f.relative_to(repo)
        comp = module_comp(rel.parts)
        module_candidates[comp] = _ModuleCandidate(f, False)

    for d in dirs_with_py:
        comp = () if d == Path(".") else d.parts
        if comp not in module_candidates:
            module_candidates[comp] = _ModuleCandidate(
                repo / d if d != Path(".") else repo, True
            )

    suffix_index: dict[_PathParts, list[_SuffixEntry]] = {}
    for comp, cand in module_candidates.items():
        for k in range(1, len(comp) + 1):
            suf = comp[-k:]
            suffix_index.setdefault(suf, []).append(
                _SuffixEntry(comp, cand.path, cand.is_namespace_dir)
            )
    frozen_suffix_index: SuffixIndex = {suf: tuple(v) for suf, v in suffix_index.items()}

    return Index(frozenset(dirs_with_py), module_candidates, frozen_suffix_index)


def path_components(p: Path) -> _PathParts:
    return () if p == Path(".") else p.parts


def _disambiguate(
    cands: Sequence[_SuffixEntry],
    importer_dir_parts: _PathParts,
    suf: _PathParts,
    module_candidates: ModuleCandidates,
) -> _Resolution:
    uniq: dict[tuple[_PathParts, Path], bool] = {}
    for entry in cands:
        uniq[(entry.comp, entry.path)] = entry.is_namespace_dir
    entries = [_SuffixEntry(comp, path, isd) for (comp, path), isd in uniq.items()]

    # Python prefers a regular package/module over a namespace-package
    # directory portion, wherever on the path the two sit: if any candidate
    # here is a real .py module or an __init__.py package, drop the
    # namespace-dir candidates before scoring.
    regular = [e for e in entries if not e.is_namespace_dir]
    if regular:
        entries = regular

    scored: list[_ScoredCandidate] = []
    for entry in entries:
        root = entry.comp[: len(entry.comp) - len(suf)]
        is_ancestor = root == importer_dir_parts[: len(root)]
        scored.append(
            _ScoredCandidate(
                len(root), is_ancestor, entry.path, root, entry.is_namespace_dir
            )
        )

    ancestors = [s for s in scored if s.is_ancestor]
    if ancestors:
        best_depth = max(s.root_depth for s in ancestors)
        best = [s for s in ancestors if s.root_depth == best_depth]
        return Resolved(best[0].path)

    # a non-ancestor root is only valid if it is not itself inside a package:
    # a module nested in someone else's package is not importable as a
    # top-level name from another project. This filter only applies to
    # single-file candidates (x.py); package candidates (x/__init__.py or a
    # namespace dir x/) skip it.
    def is_package(root: _PathParts) -> bool:
        cand = module_candidates.get(root)
        return (
            cand is not None
            and not cand.is_namespace_dir
            and cand.path.name == "__init__.py"
        )

    def is_single_file_candidate(path: Path, isd: bool) -> bool:
        return not isd and path.name != "__init__.py"

    non_package_root = [
        s
        for s in scored
        if not (
            is_single_file_candidate(s.path, s.is_namespace_dir) and is_package(s.root)
        )
    ]
    if len(non_package_root) == 1:
        return Resolved(non_package_root[0].path)

    if not non_package_root:
        return _Unresolved()

    return Ambiguous(tuple(sorted(s.path for s in non_package_root)))


def resolve_exact(
    dotted_parts: Sequence[str],
    importer_dir_parts: _PathParts,
    suffix_index: SuffixIndex,
    module_candidates: ModuleCandidates,
) -> _Resolution:
    if not dotted_parts or dotted_parts[0] in sys.stdlib_module_names:
        return _Unresolved()
    suf = tuple(dotted_parts)
    cands = suffix_index.get(suf)
    if not cands:
        return _Unresolved()
    return _disambiguate(cands, importer_dir_parts, suf, module_candidates)


def resolve_relative(
    module_parts: _PathParts,
    level: int,
    importer_dir_parts: _PathParts,
    module_candidates: ModuleCandidates,
) -> _Resolution:
    drop = level - 1
    if drop > len(importer_dir_parts):
        return _Unresolved()
    target_dir = (
        importer_dir_parts[: len(importer_dir_parts) - drop]
        if drop
        else importer_dir_parts
    )
    comp = target_dir + module_parts
    cand = module_candidates.get(comp)
    if cand is None:
        return _Unresolved()
    return Resolved(cand.path)
