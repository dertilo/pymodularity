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

from pymodularity._import_parse._import_resolve import (
    ImportRoots,
    Index,
    ModuleCandidates,
    build_index,
    path_components,
)
from pymodularity._import_parse.attribute_access import (
    PathParts,
    collect_attribute_accesses,
    locally_bound_names,
)
from pymodularity._import_parse.import_bindings import resolve_import_node
from pymodularity._import_parse.package_roots import (
    find_package_roots,
    is_path_excluded,
    should_skip_dir,
)

# (importer_rel, target_rel) file-level import edge
EdgeKey = tuple[Path, Path]
EdgeNames = Mapping[EdgeKey, frozenset[str]]
# an ambiguous-import warning: the importing file and its candidate targets
_AmbiguityWarning = tuple[Path, tuple[Path, ...]]
# a resolved absolute/relative import: target file, imported names (if any)
_ResolvedImport = tuple[Path, frozenset[str]]


def _is_test_file(name: str) -> bool:
    return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")


def _find_py_files(repo: Path, excludes: Sequence[str] = ()) -> tuple[Path, ...]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        rel_dir = Path(dirpath).relative_to(repo)
        dirnames[:] = [
            d
            for d in dirnames
            if not should_skip_dir(d) and not is_path_excluded(rel_dir / d, excludes)
        ]
        for fn in filenames:
            if (
                fn.endswith(".py")
                and not _is_test_file(fn)
                and not is_path_excluded(rel_dir / fn, excludes)
            ):
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


@dataclass(frozen=True, slots=True)
class NameBinding:
    """One name a block of statements defines: for a def/class, `node` is
    the statement itself (decorators, bases, defaults, annotations, body
    all live under it) and `is_def_like` is true; for an assignment,
    `node` is the right-hand side only, not the target, and `is_def_like`
    is false."""

    name: str
    node: ast.AST
    is_def_like: bool


def _name_bindings_from_stmt(stmt: ast.stmt) -> tuple[NameBinding, ...]:
    """Bindings one statement makes: def/async def/class, plain or
    annotated assignment (including tuple-unpacking), `type X = ...`, or
    the same recursively inside an `if`/`try`/`try*` block's branches
    (body, elif/else, except handlers, finally). Imports never count."""
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return (NameBinding(stmt.name, stmt, True),)
    if isinstance(stmt, ast.Assign):
        return tuple(
            NameBinding(name, stmt.value, False)
            for target in stmt.targets
            for name in _assign_target_names(target)
        )
    if isinstance(stmt, ast.AnnAssign):
        value = stmt.value if stmt.value is not None else stmt.annotation
        return tuple(
            NameBinding(name, value, False) for name in _assign_target_names(stmt.target)
        )
    if type(stmt).__name__ == "TypeAlias":
        # `type X = ...` (py3.12+); accessed by name, not ast.TypeAlias,
        # since this project's declared pythonVersion (3.11) doesn't know
        # that attribute, and ast.parse itself only ever yields this node
        # when running under a 3.12+ interpreter.
        name_node: object = getattr(stmt, "name", None)
        value_node: object = getattr(stmt, "value", None)
        if isinstance(name_node, ast.Name) and isinstance(value_node, ast.AST):
            return (NameBinding(name_node.id, value_node, False),)
        return ()
    if isinstance(stmt, ast.If):
        branches = (*stmt.body, *stmt.orelse)
        return tuple(b for s in branches for b in _name_bindings_from_stmt(s))
    if isinstance(stmt, (ast.Try, ast.TryStar)):
        branches = (*stmt.body, *stmt.orelse, *stmt.finalbody)
        bindings = [b for s in branches for b in _name_bindings_from_stmt(s)]
        for handler in stmt.handlers:
            bindings.extend(b for s in handler.body for b in _name_bindings_from_stmt(s))
        return tuple(bindings)
    return ()


def name_bindings(body: Sequence[ast.stmt]) -> tuple[NameBinding, ...]:
    """One `NameBinding` per name one block of statements defines, used
    both to compute `defined_names` and, in `_coupling_metrics.name_groups`, to find the
    node whose body to walk for each name's internal references."""
    return tuple(b for stmt in body for b in _name_bindings_from_stmt(stmt))


def _top_level_defined_names(tree: ast.Module) -> frozenset[str]:
    """Names this module defines at top level: def, async def, class, plain
    or annotated assignment (including tuple-unpacking), `type X = ...`, and
    the same nested in a top-level `if`/`try`/`try*` block. Imports don't
    count."""
    return frozenset(b.name for b in name_bindings(tree.body))


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


@dataclass(frozen=True, slots=True)
class _CollectedEdges:
    """What one parse of a repo's files collects: file-level import edges
    (with their imported names), the attribute-form equivalents of both
    (a module reached through an attribute chain, a private name accessed
    as `mod._x` rather than imported), the ambiguous-import warnings raised
    along the way, each file's top-level defined names, and each file's
    decorator-registered names (a subset of defined_names)."""

    edge_names: EdgeNames
    attr_module_edges: EdgeNames
    attr_name_edges: EdgeNames
    warnings: tuple[_AmbiguityWarning, ...]
    defined_names: Mapping[Path, frozenset[str]]
    registered_names: Mapping[Path, frozenset[str]]


def _collect_edges(
    repo: Path,
    py_files: Sequence[Path],
    module_candidates: ModuleCandidates,
    import_roots: ImportRoots,
) -> _CollectedEdges:
    # (importer_rel, target_rel) -> set of imported names (empty = no names,
    # e.g. a plain "import x.y" or a "from a import b" where b is itself the
    # resolved submodule rather than a name defined inside it)
    edge_names: dict[EdgeKey, set[str]] = {}
    attr_module_edges: dict[EdgeKey, set[str]] = {}
    attr_name_edges: dict[EdgeKey, set[str]] = {}
    warnings: list[_AmbiguityWarning] = []
    defined_names: dict[Path, frozenset[str]] = {}
    registered_names: dict[Path, frozenset[str]] = {}
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
        importer_dir_parts = path_components(rel.parent)
        targets: list[_ResolvedImport] = []
        # local name -> canonical dotted components of every module it's
        # bound to (usually one; a set when 2 package roots both contribute
        # a same-named top package, `import a.b; import a.c`), for every
        # import that binds a name to a module (not a value): drives the
        # attribute-form check below (`import a.b.mod` then `a.b.mod._x`,
        # `from a.b import mod` then `mod._x`, ...).
        binding: dict[str, set[PathParts]] = {}
        # dotted-prefix components a plain `import a.b.mod` (no `as`) walks
        # through on its own -- skipped as attribute-form hops, since
        # they're steps of that one import, not a second access.
        plain_import_hops: set[PathParts] = set()

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            effects = resolve_import_node(
                node, importer_dir_parts, import_roots, module_candidates, repo
            )
            targets.extend(effects.targets)
            for name, comp in effects.bindings:
                binding.setdefault(name, set()).add(comp)
            plain_import_hops.update(effects.plain_import_hops)
            warnings.extend((rel, cands) for cands in effects.ambiguous_candidates)

        imported_targets: set[Path] = set()
        imported_name_pairs: set[tuple[Path, str]] = set()
        for t, names_here in targets:
            trel = t.relative_to(repo)
            imported_targets.add(trel)
            imported_name_pairs.update((trel, n) for n in names_here)
            if trel == rel:
                continue
            edge_names.setdefault((rel, trel), set()).update(names_here)

        # Option A, conservative: a name this file also rebinds locally (a
        # parameter, assignment target, for/with/except/comprehension
        # target, nested def/class) is dropped from `binding` for the whole
        # file -- a false finding becomes a missed one.
        shadowed = locally_bound_names(tree)
        if shadowed:
            binding = {k: v for k, v in binding.items() if k not in shadowed}

        module_hops, name_accesses = collect_attribute_accesses(
            tree, binding, module_candidates, plain_import_hops
        )
        for hop in module_hops:
            trel = module_candidates[hop].path.relative_to(repo)
            # an attribute-form finding is dropped when the import form
            # already reports the same (importer, target) for a module.
            if trel != rel and trel not in imported_targets:
                attr_module_edges.setdefault((rel, trel), set())
        for current_comp, name in name_accesses:
            trel = module_candidates[current_comp].path.relative_to(repo)
            # same dedup, for the (importer, target, name) shape.
            if trel == rel or (trel, name) in imported_name_pairs:
                continue
            attr_name_edges.setdefault((rel, trel), set()).add(name)

    frozen_edge_names: EdgeNames = {k: frozenset(v) for k, v in edge_names.items()}
    frozen_attr_module_edges: EdgeNames = {
        k: frozenset(v) for k, v in attr_module_edges.items()
    }
    frozen_attr_name_edges: EdgeNames = {
        k: frozenset(v) for k, v in attr_name_edges.items()
    }
    return _CollectedEdges(
        frozen_edge_names,
        frozen_attr_module_edges,
        frozen_attr_name_edges,
        tuple(warnings),
        defined_names,
        registered_names,
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
    file-level import edges (and their imported names), their attribute-form
    equivalents, each file's top-level defined names, each file's
    decorator-registered names, and the forward/reverse file adjacency."""

    py_files: tuple[Path, ...]
    dirs_with_py: frozenset[Path]
    edge_names: EdgeNames
    attr_module_edges: EdgeNames
    attr_name_edges: EdgeNames
    defined_names: Mapping[Path, frozenset[str]]
    registered_names: Mapping[Path, frozenset[str]]
    adj: Mapping[Path, frozenset[Path]]
    radj: Mapping[Path, frozenset[Path]]


def build_graph(repo: Path, excludes: Sequence[str] = ()) -> _RepoGraph:
    """Parses `repo` once and builds every structure every subcommand needs.
    `excludes` (fnmatch patterns, relative to `repo`) prunes matching
    trees out of the walk entirely, so excluded code is invisible here and
    downstream in every section built from this graph. Prints
    ambiguous-import warnings to stderr along the way. Reach and
    file/directory reach-scores are not computed here -- `check` never
    uses them, so `graph` and `metrics` compute them in their own run_*
    path."""
    py_files = _find_py_files(repo, excludes)
    package_roots = find_package_roots(repo, excludes)
    idx: Index = build_index(repo, py_files, package_roots)
    collected = _collect_edges(repo, py_files, idx.module_candidates, idx.import_roots)

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
        collected.attr_module_edges,
        collected.attr_name_edges,
        collected.defined_names,
        collected.registered_names,
        file_graph.adj,
        file_graph.radj,
    )
