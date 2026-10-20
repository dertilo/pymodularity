"""Resolving one `ast.Import`/`ast.ImportFrom` node to its targets and the
local-name bindings it creates, factored out of `graph._collect_edges`'s ast
walk to keep that module under the line-count soft cap. One call per node;
the caller merges the result into its own per-file accumulators."""

import ast
from dataclasses import dataclass, field
from pathlib import Path

from pymodularity._parse._resolve import (
    Ambiguous,
    ImportRoots,
    ModuleCandidates,
    Resolved,
    resolve_exact,
    resolve_relative,
)
from pymodularity._parse.attrs import (
    PathParts,
    bound_module_comp,
    plain_import_anchor,
    plain_import_path_hops,
)

# a resolved import target: the file, and any names imported from it
_ResolvedTarget = tuple[Path, frozenset[str]]
# local name -> dotted components of the module it's bound to
_Binding = tuple[str, PathParts]


@dataclass(frozen=True, slots=True)
class ImportEffects:
    """What resolving one import node produces: its resolved targets, the
    module bindings it creates (for the attribute-form check), the plain
    import's own dotted-path hops (skipped later as attribute-form hops),
    and the repo-relative paths of any ambiguous candidates found."""

    targets: tuple[_ResolvedTarget, ...] = ()
    bindings: tuple[_Binding, ...] = ()
    plain_import_hops: frozenset[PathParts] = field(default_factory=frozenset)
    ambiguous_candidates: tuple[tuple[Path, ...], ...] = ()


def _resolve_import(
    node: ast.Import,
    importer_dir_parts: tuple[str, ...],
    import_roots: ImportRoots,
    module_candidates: ModuleCandidates,
    repo: Path,
) -> ImportEffects:
    targets: list[_ResolvedTarget] = []
    bindings: list[_Binding] = []
    plain_import_hops: set[PathParts] = set()
    ambiguous: list[tuple[Path, ...]] = []
    for alias in node.names:
        dotted = alias.name.split(".")
        res = resolve_exact(dotted, importer_dir_parts, import_roots, module_candidates)
        if isinstance(res, Ambiguous):
            ambiguous.append(res.candidates)
        elif isinstance(res, Resolved):
            targets.append((res.path, frozenset()))
            canonical = bound_module_comp(res.path, repo)
            if alias.asname:
                bindings.append((alias.asname, canonical))
            else:
                bindings.append((dotted[0], plain_import_anchor(dotted, canonical)))
                plain_import_hops.update(plain_import_path_hops(dotted, canonical))
    return ImportEffects(
        tuple(targets), tuple(bindings), frozenset(plain_import_hops), tuple(ambiguous)
    )


def _resolve_relative_import_from(
    node: ast.ImportFrom,
    importer_dir_parts: tuple[str, ...],
    module_candidates: ModuleCandidates,
    repo: Path,
) -> ImportEffects:
    targets: list[_ResolvedTarget] = []
    bindings: list[_Binding] = []
    if node.module:
        # "from ..pkg import mod[, mod2, ...]": each alias might itself be a
        # submodule of "pkg" (binds its local name, like the absolute form)
        # or a name defined inside "pkg" (doesn't bind a module).
        module_parts = tuple(node.module.split("."))
        for alias in node.names:
            res = resolve_relative(
                module_parts + (alias.name,),
                node.level,
                importer_dir_parts,
                module_candidates,
            )
            if isinstance(res, Resolved):
                targets.append((res.path, frozenset()))
                bindings.append(
                    (alias.asname or alias.name, bound_module_comp(res.path, repo))
                )
                continue
            res = resolve_relative(
                module_parts, node.level, importer_dir_parts, module_candidates
            )
            if isinstance(res, Resolved):
                targets.append((res.path, frozenset({alias.name})))
    else:
        # "from . import mod[, mod2, ...]": each alias, on its own, is
        # itself the submodule being resolved, so a successful resolution
        # also binds that alias's local name.
        for alias in node.names:
            res = resolve_relative(
                (alias.name,), node.level, importer_dir_parts, module_candidates
            )
            if isinstance(res, Resolved):
                targets.append((res.path, frozenset()))
                bindings.append(
                    (alias.asname or alias.name, bound_module_comp(res.path, repo))
                )
    return ImportEffects(tuple(targets), tuple(bindings))


def _resolve_absolute_import_from(
    node: ast.ImportFrom,
    importer_dir_parts: tuple[str, ...],
    import_roots: ImportRoots,
    module_candidates: ModuleCandidates,
    repo: Path,
) -> ImportEffects:
    targets: list[_ResolvedTarget] = []
    bindings: list[_Binding] = []
    ambiguous: list[tuple[Path, ...]] = []
    module_parts = node.module.split(".") if node.module else []
    for alias in node.names:
        res = resolve_exact(
            module_parts + [alias.name],
            importer_dir_parts,
            import_roots,
            module_candidates,
        )
        if isinstance(res, Ambiguous):
            ambiguous.append(res.candidates)
            continue
        if isinstance(res, Resolved):
            targets.append((res.path, frozenset()))
            bindings.append(
                (alias.asname or alias.name, bound_module_comp(res.path, repo))
            )
            continue
        res = resolve_exact(
            module_parts, importer_dir_parts, import_roots, module_candidates
        )
        if isinstance(res, Ambiguous):
            ambiguous.append(res.candidates)
        elif isinstance(res, Resolved):
            targets.append((res.path, frozenset({alias.name})))
    return ImportEffects(
        tuple(targets), tuple(bindings), ambiguous_candidates=tuple(ambiguous)
    )


def resolve_import_node(
    node: ast.Import | ast.ImportFrom,
    importer_dir_parts: tuple[str, ...],
    import_roots: ImportRoots,
    module_candidates: ModuleCandidates,
    repo: Path,
) -> ImportEffects:
    """Resolves one `ast.Import`/`ast.ImportFrom` node: its targets, the
    local-name bindings it creates, a plain import's own dotted-path hops,
    and any ambiguous-import candidates."""
    if isinstance(node, ast.Import):
        return _resolve_import(
            node, importer_dir_parts, import_roots, module_candidates, repo
        )
    if node.level and node.level > 0:
        return _resolve_relative_import_from(
            node, importer_dir_parts, module_candidates, repo
        )
    return _resolve_absolute_import_from(
        node, importer_dir_parts, import_roots, module_candidates, repo
    )
