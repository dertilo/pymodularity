"""File discovery and file-level edge collection: build the whole-repo
Python import graph with ast (stdlib only), driving `_resolve`'s index and
resolution functions through one ast walk per file, plus the
`build_graph` entry point every subcommand calls. `EdgeKey`/`EdgeNames`,
the file-level edge types every downstream module shares, live here too."""

import ast
import os
import sys
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from underscope._parse._resolve import (
    Ambiguous,
    Index,
    ModuleCandidates,
    Resolved,
    SuffixIndex,
    build_index,
    path_components,
    resolve_exact,
    resolve_relative,
)

# (importer_rel, target_rel) file-level import edge
EdgeKey = tuple[Path, Path]
EdgeNames = Mapping[EdgeKey, frozenset[str]]
# an ambiguous-import warning: the importing file and its candidate targets
_AmbiguityWarning = tuple[Path, tuple[Path, ...]]
# a resolved absolute/relative import: target file, imported names (if any)
_ResolvedImport = tuple[Path, frozenset[str]]

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


def _assign_target_names(target: ast.expr) -> tuple[str, ...]:
    """Names bound by one assignment target, recursing into tuple/list
    unpacking (including a starred element)."""
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, (ast.Tuple, ast.List)):
        names: list[str] = []
        for elt in target.elts:
            inner = elt.value if isinstance(elt, ast.Starred) else elt
            names.extend(_assign_target_names(inner))
        return tuple(names)
    return ()


def _names_from_stmt(stmt: ast.stmt) -> tuple[str, ...]:
    """Names one statement defines: def/async def/class, plain or annotated
    assignment (including tuple-unpacking), `type X = ...`, or the same
    recursively inside an `if`/`try` block's branches (body, elif/else,
    except handlers, finally). Imports never count."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return (stmt.name,)
    if isinstance(stmt, ast.Assign):
        names: list[str] = []
        for target in stmt.targets:
            names.extend(_assign_target_names(target))
        return tuple(names)
    if isinstance(stmt, ast.AnnAssign):
        return _assign_target_names(stmt.target)
    if type(stmt).__name__ == "TypeAlias":
        # `type X = ...` (py3.12+); accessed by name, not ast.TypeAlias,
        # since this project's declared pythonVersion (3.11) doesn't know
        # that attribute, and ast.parse itself only ever yields this node
        # when running under a 3.12+ interpreter.
        name_node: object = getattr(stmt, "name", None)
        return (name_node.id,) if isinstance(name_node, ast.Name) else ()
    if isinstance(stmt, ast.If):
        return tuple(n for s in (*stmt.body, *stmt.orelse) for n in _names_from_stmt(s))
    if isinstance(stmt, ast.Try):
        branches = (*stmt.body, *stmt.orelse, *stmt.finalbody)
        names = [n for s in branches for n in _names_from_stmt(s)]
        for handler in stmt.handlers:
            names.extend(n for s in handler.body for n in _names_from_stmt(s))
        return tuple(names)
    return ()


def _top_level_defined_names(tree: ast.Module) -> frozenset[str]:
    """Names this module defines at top level: def, async def, class, plain
    or annotated assignment (including tuple-unpacking), `type X = ...`, and
    the same nested in a top-level `if`/`try` block. Imports don't count."""
    defined: set[str] = set()
    for stmt in tree.body:
        defined.update(_names_from_stmt(stmt))
    return frozenset(defined)


def _registered_names_from_stmt(stmt: ast.stmt) -> tuple[str, ...]:
    """Names of top-level functions (not classes) a decorator registers --
    FastAPI routes, Typer/Click commands, Dagster assets, pytest fixtures --
    including the same nested in a top-level `if`/`try` block. Any
    decorator counts; there's no list of framework names."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return (stmt.name,) if stmt.decorator_list else ()
    if isinstance(stmt, ast.If):
        return tuple(
            n for s in (*stmt.body, *stmt.orelse) for n in _registered_names_from_stmt(s)
        )
    if isinstance(stmt, ast.Try):
        branches = (*stmt.body, *stmt.orelse, *stmt.finalbody)
        names = [n for s in branches for n in _registered_names_from_stmt(s)]
        for handler in stmt.handlers:
            names.extend(n for s in handler.body for n in _registered_names_from_stmt(s))
        return tuple(names)
    return ()


def _top_level_registered_names(tree: ast.Module) -> frozenset[str]:
    """Names this module's top-level decorated functions define (not
    classes -- `@dataclass` sits on most models), including the same
    nested in a top-level `if`/`try` block."""
    registered: set[str] = set()
    for stmt in tree.body:
        registered.update(_registered_names_from_stmt(stmt))
    return frozenset(registered)


def _names_loaded_in_file(tree: ast.Module) -> frozenset[str]:
    """Every `ast.Name` id referenced (Load context) anywhere in the file,
    at any nesting depth -- used to tell a dead unused-public name from one
    only ever used inside its own file. A string annotation (e.g.
    `"Embedder"`) is not an `ast.Name` and so doesn't count as a use; a
    recursive function's self-call does."""
    return frozenset(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    )


@dataclass(frozen=True, slots=True)
class _CollectedEdges:
    """What one parse of a repo's files collects: file-level import edges
    (with their imported names), the ambiguous-import warnings raised along
    the way, each file's top-level defined names, each file's
    decorator-registered names (a subset of defined_names), and each file's
    loaded (Load-context) `ast.Name` ids anywhere in the file."""

    edge_names: EdgeNames
    warnings: tuple[_AmbiguityWarning, ...]
    defined_names: Mapping[Path, frozenset[str]]
    registered_names: Mapping[Path, frozenset[str]]
    loaded_names: Mapping[Path, frozenset[str]]


def _collect_edges(
    repo: Path,
    py_files: Sequence[Path],
    module_candidates: ModuleCandidates,
    suffix_index: SuffixIndex,
) -> _CollectedEdges:
    # (importer_rel, target_rel) -> set of imported names (empty = no names,
    # e.g. a plain "import x.y" or a "from a import b" where b is itself the
    # resolved submodule rather than a name defined inside it)
    edge_names: dict[EdgeKey, set[str]] = {}
    warnings: list[_AmbiguityWarning] = []
    defined_names: dict[Path, frozenset[str]] = {}
    registered_names: dict[Path, frozenset[str]] = {}
    loaded_names: dict[Path, frozenset[str]] = {}
    for f in sorted(py_files):
        rel = f.relative_to(repo)
        try:
            text = f.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(f))
        except (SyntaxError, UnicodeDecodeError) as e:
            print(f"warning: skipping {rel}: {e}", file=sys.stderr)
            continue

        defined_names[rel] = _top_level_defined_names(tree)
        registered_names[rel] = _top_level_registered_names(tree)
        loaded_names[rel] = _names_loaded_in_file(tree)
        importer_dir_parts = path_components(rel.parent)
        targets: list[_ResolvedImport] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    dotted = alias.name.split(".")
                    res = resolve_exact(
                        dotted, importer_dir_parts, suffix_index, module_candidates
                    )
                    if isinstance(res, Ambiguous):
                        warnings.append((rel, res.candidates))
                    elif isinstance(res, Resolved):
                        targets.append((res.path, frozenset()))
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    requests: list[tuple[tuple[str, ...], frozenset[str]]]
                    if node.module:
                        requests = [
                            (
                                tuple(node.module.split(".")),
                                frozenset(alias.name for alias in node.names),
                            )
                        ]
                    else:
                        requests = [((alias.name,), frozenset()) for alias in node.names]
                    for module_parts, names_here in requests:
                        res = resolve_relative(
                            module_parts,
                            node.level,
                            importer_dir_parts,
                            module_candidates,
                        )
                        if isinstance(res, Resolved):
                            targets.append((res.path, names_here))
                else:
                    module_parts = node.module.split(".") if node.module else []
                    for alias in node.names:
                        res = resolve_exact(
                            module_parts + [alias.name],
                            importer_dir_parts,
                            suffix_index,
                            module_candidates,
                        )
                        if isinstance(res, Ambiguous):
                            warnings.append((rel, res.candidates))
                            continue
                        if isinstance(res, Resolved):
                            targets.append((res.path, frozenset()))
                            continue
                        res = resolve_exact(
                            module_parts,
                            importer_dir_parts,
                            suffix_index,
                            module_candidates,
                        )
                        if isinstance(res, Ambiguous):
                            warnings.append((rel, res.candidates))
                        elif isinstance(res, Resolved):
                            targets.append((res.path, frozenset({alias.name})))

        for t, names_here in targets:
            trel = t.relative_to(repo)
            if trel == rel:
                continue
            edge_names.setdefault((rel, trel), set()).update(names_here)

    frozen_edge_names: EdgeNames = {k: frozenset(v) for k, v in edge_names.items()}
    return _CollectedEdges(
        frozen_edge_names,
        tuple(warnings),
        defined_names,
        registered_names,
        loaded_names,
    )


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
    top-level defined names, each file's decorator-registered names, each
    file's loaded names, and the forward/reverse file adjacency."""

    py_files: tuple[Path, ...]
    dirs_with_py: frozenset[Path]
    edge_names: EdgeNames
    defined_names: Mapping[Path, frozenset[str]]
    registered_names: Mapping[Path, frozenset[str]]
    loaded_names: Mapping[Path, frozenset[str]]
    adj: Mapping[Path, frozenset[Path]]
    radj: Mapping[Path, frozenset[Path]]


def build_graph(repo: Path) -> _RepoGraph:
    """Parses `repo` once and builds every structure every subcommand needs.
    Prints ambiguous-import warnings to stderr along the way. Reach and
    file/directory scores are not computed here -- `check` never uses
    them, so `graph` and `metrics` compute them in their own run_*
    path."""
    py_files = _find_py_files(repo)
    idx: Index = build_index(repo, py_files)
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
        collected.registered_names,
        collected.loaded_names,
        file_graph.adj,
        file_graph.radj,
    )
