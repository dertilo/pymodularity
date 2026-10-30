"""Name groups: a file's top-level names clustered by who uses them
together, with a re-export fix so an import reached through a package
`__init__.py` counts against the file that actually defines the name."""

import ast
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from pymodularity._import_parse.attribute_access import locally_bound_names
from pymodularity._import_parse.import_graph import EdgeNames, name_bindings


def _globally_declared_names(node: ast.AST) -> frozenset[str]:
    """Every name named in a `global` statement anywhere under `node`.
    Such a name is not locally bound: `global x; x = 1` reassigns the
    module-level `x`, not a name local to `node`. `nonlocal` is
    irrelevant here since `node` is a def/class at module scope."""
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Global):
            names.update(sub.names)
    return frozenset(names)


def _referenced_identifiers(node: ast.AST) -> frozenset[str]:
    """ast.Name ids anywhere under `node` (`ast.walk` already visits the
    base `ast.Name` of an `ast.Attribute`, so no separate branch is
    needed), minus names `node` itself binds locally: a parameter, or an
    assignment/for/with/except/comprehension target inside it. Without
    the subtraction, `def a(b): return b` next to `def b(): ...` would
    join `a` and `b` through the parameter, not a real reference to the
    top-level `b`. A name `node` declares `global` is excluded from that
    subtraction: it is the module global being referenced, not a local
    binding, so a getter/setter pair sharing a `global` name still joins
    through it."""
    found = {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name)}
    bound = locally_bound_names(node) - _globally_declared_names(node)
    return frozenset(found - bound)


@dataclass(slots=True)
class _UnionFind:
    """Owned, single-function mutable state: a name-to-name disjoint-set
    forest private to one `compute_name_groups` call, never aliased or
    read across an `await`."""

    # nosemgrep: no-mutable-collections-in-attributes  # boundary: owned, private to one call
    parent: dict[str, str]

    @classmethod
    def _of(cls, items: AbstractSet[str]) -> "_UnionFind":
        return cls({x: x for x in items})

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


@dataclass(frozen=True, slots=True)
class _NameGroup:
    """One joined cluster of a file's top-level names, and the files
    that import any name in it."""

    names: tuple[str, ...]
    importers: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _FileNameGroups:
    """A reported file's counting name groups (>= 1 def/class each),
    sorted largest-importer-count first."""

    path: Path
    groups: tuple[_NameGroup, ...]


def _resolved_importers_by_target(
    defined_names: Mapping[Path, AbstractSet[str]],
    edge_names: EdgeNames,
    attr_name_edges: EdgeNames,
) -> Mapping[Path, Mapping[Path, AbstractSet[str]]]:
    """Per target file: {importer -> names imported from it}, with an
    import reached through a package `__init__.py` re-attributed to the
    file that actually defines the name, following a chain of `__init__.py`
    re-exports (each one's own import of that same name from elsewhere in
    the repo) until a file defines the name or the chain cycles back on
    itself."""
    from_init: dict[Path, list[tuple[Path, frozenset[str]]]] = {}
    for (imp, tgt), names in edge_names.items():
        if imp.name == "__init__.py":
            from_init.setdefault(imp, []).append((tgt, names))

    def resolve(tgt: Path, name: str) -> Path:
        visited: set[Path] = set()
        while tgt not in visited:
            visited.add(tgt)
            if name in defined_names.get(tgt, frozenset()):
                return tgt
            next_tgt = None
            for tgt2, names2 in from_init.get(tgt, ()):
                if name in names2:
                    next_tgt = tgt2
                    break
            if next_tgt is None:
                return tgt
            tgt = next_tgt
        return tgt

    importers_by_target: dict[Path, dict[Path, set[str]]] = {}

    for (imp, tgt), names in edge_names.items():
        if imp.name == "__init__.py":
            continue
        for name in names:
            rtgt = resolve(tgt, name)
            importers_by_target.setdefault(rtgt, {}).setdefault(imp, set()).add(name)

    for (imp, tgt), names in attr_name_edges.items():
        if imp.name == "__init__.py":
            continue
        for name in names:
            rtgt = resolve(tgt, name)
            importers_by_target.setdefault(rtgt, {}).setdefault(imp, set()).add(name)

    return {
        tgt: {imp: frozenset(names) for imp, names in importers.items()}
        for tgt, importers in importers_by_target.items()
    }


def compute_name_groups(
    repo: Path,
    py_files: Sequence[Path],
    defined_names: Mapping[Path, AbstractSet[str]],
    edge_names: EdgeNames,
    attr_name_edges: EdgeNames,
) -> tuple[_FileNameGroups, ...]:
    """Per file (`__init__.py` excluded): join top-level names when one
    importer imports them together, or one name's body references the
    other. A group only counts when it holds at least one `def`,
    `async def` or `class` name. A file is reported when it has >= 2
    counting groups. An import of a name through a package `__init__.py`
    is attributed to the file that defines the name, not the
    `__init__.py` itself -- see module docstring."""
    importers_by_target = _resolved_importers_by_target(
        defined_names, edge_names, attr_name_edges
    )

    results: list[_FileNameGroups] = []
    for f in py_files:
        rel = f.relative_to(repo)
        if rel.name == "__init__.py":
            continue
        defined = defined_names.get(rel, frozenset())
        if len(defined) < 2:
            continue

        try:
            text = f.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(f))
        except (SyntaxError, UnicodeDecodeError):
            continue

        name_nodes = [nn for nn in name_bindings(tree.body) if nn.name in defined]
        def_like = {nn.name for nn in name_nodes if nn.is_def_like}

        uf = _UnionFind._of(defined)

        for nn in name_nodes:
            refs = _referenced_identifiers(nn.node)
            for other in defined:
                if other != nn.name and other in refs:
                    uf.union(nn.name, other)

        importer_names = importers_by_target.get(rel, {})
        for names_here in importer_names.values():
            names_here = names_here & defined
            if not names_here:
                continue
            names_list = list(names_here)
            for other in names_list[1:]:
                uf.union(names_list[0], other)

        comp_names: dict[str, set[str]] = {}
        for n in defined:
            comp_names.setdefault(uf.find(n), set()).add(n)

        groups: list[_NameGroup] = []
        for names_set in comp_names.values():
            importers_here: set[Path] = set()
            touched = False
            for imp, names_here in importer_names.items():
                if names_here & names_set:
                    importers_here.add(imp)
                    touched = True
            if not touched or not (names_set & def_like):
                continue
            groups.append(
                _NameGroup(tuple(sorted(names_set)), tuple(sorted(importers_here)))
            )

        if len(groups) < 2:
            continue

        groups.sort(key=lambda g: (-len(g.importers), g.names))
        results.append(_FileNameGroups(rel, tuple(groups)))

    results.sort(key=lambda r: str(r.path))
    return tuple(results)
