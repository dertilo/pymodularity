"""File discovery, import resolution and file-level edge collection: build
the whole-repo Python import graph with ast (stdlib only), plus the
`_build_file_graph`/`build_graph` entry points every subcommand calls.
Shared types produced by resolution (_PathParts, EdgeNames, ...) live
here too."""

import ast
import os
import sys
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

# dotted module path components, e.g. ("shop", "billing", "pricing")
_PathParts = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ModuleCandidate:
    """A resolvable module: its file/dir path, and whether it's a namespace
    package directory (True) or a single .py file / __init__.py (False)."""

    path: Path
    is_namespace_dir: bool


_ModuleCandidates = Mapping[_PathParts, _ModuleCandidate]


@dataclass(frozen=True, slots=True)
class _SuffixEntry:
    """A module candidate indexed by dotted suffix."""

    comp: _PathParts
    path: Path
    is_namespace_dir: bool


_SuffixIndex = Mapping[_PathParts, tuple[_SuffixEntry, ...]]
# an ambiguous-import warning: the importing file and its candidate targets
_AmbiguityWarning = tuple[Path, tuple[Path, ...]]
# (importer_rel, target_rel) file-level import edge
EdgeKey = tuple[Path, Path]
EdgeNames = Mapping[EdgeKey, frozenset[str]]
# a resolved absolute/relative import: target file, imported names (if any)
_ResolvedImport = tuple[Path, frozenset[str]]


@dataclass(frozen=True, slots=True)
class _ScoredCandidate:
    """A module candidate scored for disambiguation against the importer."""

    root_depth: int
    is_ancestor: bool
    path: Path
    root: _PathParts
    is_namespace_dir: bool


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


def _is_test_file(name: str) -> bool:
    return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")


def _find_py_files(repo: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        for fn in filenames:
            if fn.endswith(".py") and not _is_test_file(fn):
                files.append(Path(dirpath) / fn)
    return tuple(files)


def module_comp(parts: tuple[str, ...]) -> _PathParts:
    if parts[-1] == "__init__.py":
        return parts[:-1]
    return parts[:-1] + (parts[-1][:-3],)


@dataclass(frozen=True, slots=True)
class _Index:
    """What a repo's file list indexes into: the directories holding Python
    code, every resolvable module candidate, and that candidate set indexed
    by dotted suffix for lookup."""

    dirs_with_py: frozenset[Path]
    module_candidates: _ModuleCandidates
    suffix_index: _SuffixIndex


def _build_index(repo: Path, py_files: Sequence[Path]) -> _Index:
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
    frozen_suffix_index: _SuffixIndex = {suf: tuple(v) for suf, v in suffix_index.items()}

    return _Index(frozenset(dirs_with_py), module_candidates, frozen_suffix_index)


def _path_components(p: Path) -> _PathParts:
    return () if p == Path(".") else p.parts


# nosemgrep: no-mutable-collections-in-signatures  # warnings accumulator: appended across many resolution calls sharing one _collect_edges pass
def _disambiguate(
    cands: Sequence[_SuffixEntry],
    importer_dir_parts: _PathParts,
    suf: _PathParts,
    importer_rel: Path,
    warnings: list[_AmbiguityWarning],
    module_candidates: _ModuleCandidates,
) -> Path | None:
    uniq: dict[tuple[_PathParts, Path], bool] = {}
    for entry in cands:
        uniq[(entry.comp, entry.path)] = entry.is_namespace_dir
    entries = [_SuffixEntry(comp, path, isd) for (comp, path), isd in uniq.items()]

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
        return best[0].path

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
        return non_package_root[0].path

    if not non_package_root:
        return None

    warnings.append((importer_rel, tuple(s.path for s in non_package_root)))
    return None


# nosemgrep: no-mutable-collections-in-signatures  # warnings accumulator: appended across many resolution calls sharing one _collect_edges pass
def _resolve_exact(
    dotted_parts: Sequence[str],
    importer_dir_parts: _PathParts,
    suffix_index: _SuffixIndex,
    importer_rel: Path,
    warnings: list[_AmbiguityWarning],
    module_candidates: _ModuleCandidates,
) -> Path | None:
    if not dotted_parts or dotted_parts[0] in sys.stdlib_module_names:
        return None
    suf = tuple(dotted_parts)
    cands = suffix_index.get(suf)
    if not cands:
        return None
    return _disambiguate(
        cands,
        importer_dir_parts,
        suf,
        importer_rel,
        warnings,
        module_candidates,
    )


def _resolve_relative(
    module: str | None,
    level: int,
    names: Sequence[ast.alias],
    importer_dir_parts: _PathParts,
    module_candidates: _ModuleCandidates,
) -> tuple[_ResolvedImport, ...]:
    drop = level - 1
    if drop > len(importer_dir_parts):
        return ()
    target_dir = (
        importer_dir_parts[: len(importer_dir_parts) - drop]
        if drop
        else importer_dir_parts
    )

    results: list[_ResolvedImport] = []
    if module:
        comp = target_dir + tuple(module.split("."))
        cand = module_candidates.get(comp)
        if cand:
            names_here = frozenset(alias.name for alias in names)
            results.append((cand.path, names_here))
    else:
        for alias in names:
            comp = target_dir + (alias.name,)
            cand = module_candidates.get(comp)
            if cand:
                results.append((cand.path, frozenset()))
    return tuple(results)


def _top_level_defined_names(tree: ast.Module) -> frozenset[str]:
    """Names this module defines at top level: def, async def, class, and
    plain or annotated assignment to a bare name. Imports don't count."""
    defined: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    defined.add(target.id)
        elif isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            defined.add(stmt.target.id)
    return frozenset(defined)


@dataclass(frozen=True, slots=True)
class _CollectedEdges:
    """What one parse of a repo's files collects: file-level import edges
    (with their imported names), the ambiguous-import warnings raised along
    the way, and each file's top-level defined names."""

    edge_names: EdgeNames
    warnings: tuple[_AmbiguityWarning, ...]
    defined_names: Mapping[Path, frozenset[str]]


def _collect_edges(
    repo: Path,
    py_files: Sequence[Path],
    module_candidates: _ModuleCandidates,
    suffix_index: _SuffixIndex,
) -> _CollectedEdges:
    # (importer_rel, target_rel) -> set of imported names (empty = no names,
    # e.g. a plain "import x.y" or a "from a import b" where b is itself the
    # resolved submodule rather than a name defined inside it)
    edge_names: dict[EdgeKey, set[str]] = {}
    warnings: list[_AmbiguityWarning] = []
    defined_names: dict[Path, frozenset[str]] = {}
    for f in sorted(py_files):
        rel = f.relative_to(repo)
        try:
            text = f.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(f))
        except (SyntaxError, UnicodeDecodeError) as e:
            print(f"warning: skipping {rel}: {e}", file=sys.stderr)
            continue

        defined_names[rel] = _top_level_defined_names(tree)
        importer_dir_parts = _path_components(rel.parent)
        targets: list[_ResolvedImport] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    dotted = alias.name.split(".")
                    t = _resolve_exact(
                        dotted,
                        importer_dir_parts,
                        suffix_index,
                        rel,
                        warnings,
                        module_candidates,
                    )
                    if t is not None:
                        targets.append((t, frozenset()))
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    targets.extend(
                        _resolve_relative(
                            node.module,
                            node.level,
                            node.names,
                            importer_dir_parts,
                            module_candidates,
                        )
                    )
                else:
                    module_parts = node.module.split(".") if node.module else []
                    for alias in node.names:
                        t = _resolve_exact(
                            module_parts + [alias.name],
                            importer_dir_parts,
                            suffix_index,
                            rel,
                            warnings,
                            module_candidates,
                        )
                        if t is not None:
                            targets.append((t, frozenset()))
                            continue
                        t = _resolve_exact(
                            module_parts,
                            importer_dir_parts,
                            suffix_index,
                            rel,
                            warnings,
                            module_candidates,
                        )
                        if t is not None:
                            targets.append((t, frozenset({alias.name})))

        for t, names_here in targets:
            trel = t.relative_to(repo)
            if trel == rel:
                continue
            edge_names.setdefault((rel, trel), set()).update(names_here)

    frozen_edge_names: EdgeNames = {k: frozenset(v) for k, v in edge_names.items()}
    return _CollectedEdges(frozen_edge_names, tuple(warnings), defined_names)


def is_private_name(name: str) -> bool:
    """A name private by Rust's convention, ported to PEP 8 underscores:
    one leading underscore, not a dunder, not the bare `_` placeholder
    name."""
    return name.startswith("_") and not name.startswith("__") and name != "_"


@dataclass(frozen=True, slots=True)
class _FileGraph:
    """The raw file-level import graph: forward adjacency and its reverse."""

    adj: Mapping[Path, frozenset[Path]]
    radj: Mapping[Path, frozenset[Path]]


def _build_file_graph(nodes: AbstractSet[Path], edge_names: EdgeNames) -> _FileGraph:
    """Same edges `check`'s cycle rule uses: no self-edges (_collect_edges
    already drops those), duplicates already collapsed by edge_names' dict
    keys."""
    adj: dict[Path, set[Path]] = {n: set() for n in nodes}
    radj: dict[Path, set[Path]] = {n: set() for n in nodes}
    for importer_rel, target_rel in edge_names:
        if importer_rel in adj and target_rel in adj and importer_rel != target_rel:
            adj[importer_rel].add(target_rel)
            radj[target_rel].add(importer_rel)
    frozen_adj = {n: frozenset(s) for n, s in adj.items()}
    frozen_radj = {n: frozenset(s) for n, s in radj.items()}
    return _FileGraph(frozen_adj, frozen_radj)


@dataclass(frozen=True, slots=True)
class _RepoGraph:
    """Every structure every subcommand needs, built once from one parse of
    `repo`: the file list, the directories holding Python code, the
    file-level import edges (and their imported names), each file's
    top-level defined names, and the forward/reverse file adjacency."""

    py_files: tuple[Path, ...]
    dirs_with_py: frozenset[Path]
    edge_names: EdgeNames
    defined_names: Mapping[Path, frozenset[str]]
    adj: Mapping[Path, frozenset[Path]]
    radj: Mapping[Path, frozenset[Path]]


def build_graph(repo: Path) -> _RepoGraph:
    """Parses `repo` once and builds every structure every subcommand needs.
    Prints ambiguous-import warnings to stderr along the way. Reach and
    file/directory scores are not computed here -- `check` never uses
    them, so `graph` and `metrics` compute them in their own run_*
    path."""
    py_files = _find_py_files(repo)
    idx = _build_index(repo, py_files)
    collected = _collect_edges(repo, py_files, idx.module_candidates, idx.suffix_index)

    for importer_rel, cands in collected.warnings:
        cand_strs = ", ".join(str(c.relative_to(repo)) for c in cands)
        print(
            f"warning: ambiguous import in {importer_rel}: candidates {cand_strs}",
            file=sys.stderr,
        )

    nodes = frozenset(f.relative_to(repo) for f in py_files)
    file_graph = _build_file_graph(nodes, collected.edge_names)

    return _RepoGraph(
        py_files,
        idx.dirs_with_py,
        collected.edge_names,
        collected.defined_names,
        file_graph.adj,
        file_graph.radj,
    )
