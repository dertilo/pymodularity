"""The module index (`Index`/`build_index`) and import resolution
(`resolve_exact`/`resolve_relative`, returning the `Resolved | Ambiguous |
_Unresolved` sum) that `graph._collect_edges` drives one ast walk with.

`resolve_exact` resolves an absolute dotted import only against explicit
import roots: the repo root, every package root (a directory whose
pyproject.toml has a [project] table), and each root's `src/` subdirectory
if present. There is no repo-wide suffix guess -- a module is only
reachable through one of these roots, matching how Python itself resolves
`sys.path` entries. The one gap this leaves: a script that imports a
sibling file by relying on its own directory being first on `sys.path`
(e.g. `python main.py` importing `execution.py`, with no pyproject.toml
anywhere nearby) is not a package root and so is not resolved."""

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pymodularity._import_parse.module_names import module_comp

# dotted module path components, e.g. ("shop", "billing", "pricing")
_PathParts = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ModuleCandidate:
    """A resolvable module: its file/dir path, and whether it's a namespace
    package directory (True) or a single .py file / __init__.py (False)."""

    path: Path
    is_namespace_dir: bool


ModuleCandidates = Mapping[_PathParts, _ModuleCandidate]

# dotted-path components of one import root (repo root is (), a package
# root is its repo-relative parts, each root's src/ layout adds + ("src",))
ImportRoots = tuple[_PathParts, ...]


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
class Index:
    """What a repo's file list indexes into: the directories holding Python
    code, every resolvable module candidate, and the import roots absolute
    imports resolve against."""

    dirs_with_py: frozenset[Path]
    module_candidates: ModuleCandidates
    import_roots: ImportRoots


def build_index(
    repo: Path, py_files: Sequence[Path], package_roots: Sequence[Path]
) -> Index:
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

    import_roots: set[_PathParts] = {()}
    for root in package_roots:
        parts = path_components(root)
        import_roots.add(parts)
        if (repo / root / "src").is_dir():
            import_roots.add((*parts, "src"))

    return Index(frozenset(dirs_with_py), module_candidates, tuple(import_roots))


def path_components(p: Path) -> _PathParts:
    return () if p == Path(".") else p.parts


def resolve_exact(
    dotted_parts: Sequence[str],
    importer_dir_parts: _PathParts,
    import_roots: ImportRoots,
    module_candidates: ModuleCandidates,
) -> _Resolution:
    """Resolves one absolute dotted import against the repo's import
    roots: the repo root, each package root, and each root's `src/` layout
    (see module docstring). A regular module/package beats a namespace-dir
    candidate at another root; among what's left, the hit whose root is
    the deepest ancestor of the importer wins; still several -> Ambiguous;
    none -> unresolved."""
    if not dotted_parts or dotted_parts[0] in sys.stdlib_module_names:
        return _Unresolved()
    dotted = tuple(dotted_parts)
    hits: list[tuple[_PathParts, _ModuleCandidate]] = []
    for root in import_roots:
        cand = module_candidates.get(root + dotted)
        if cand is not None:
            hits.append((root, cand))
    if not hits:
        return _Unresolved()

    regular = [h for h in hits if not h[1].is_namespace_dir]
    pool = regular or hits

    unique_paths = {cand.path for _, cand in pool}
    if len(unique_paths) == 1:
        return Resolved(next(iter(unique_paths)))

    ancestors = [h for h in pool if h[0] == importer_dir_parts[: len(h[0])]]
    if ancestors:
        best_depth = max(len(root) for root, _ in ancestors)
        best_paths = {cand.path for root, cand in ancestors if len(root) == best_depth}
        if len(best_paths) == 1:
            return Resolved(next(iter(best_paths)))
        return Ambiguous(tuple(sorted(best_paths)))

    return Ambiguous(tuple(sorted(unique_paths)))


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
