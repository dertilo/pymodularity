"""The attribute form of the privacy rule: `import a.b.mod; a.b.mod._x`,
`import a; a._internal.x`, or any other dotted-attribute access reaching a
private module or name through a name an import already bound, rather than
one written directly in an import statement. Driven by `graph._collect_edges`
from the same per-file ast walk, after it builds each file's `binding` map
(local name -> the dotted components of the module an import bound it to)."""

import ast
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from pathlib import Path

from pymodularity._import_parse._import_resolve import ModuleCandidates
from pymodularity._import_parse.module_names import module_comp

# dotted module-path components, canonical form (module_comp of a file's
# repo-relative path), used as a key into ModuleCandidates
PathParts = tuple[str, ...]


def bound_module_comp(path: Path, repo: Path) -> PathParts:
    """The canonical dotted components a resolved import target binds a
    local name to."""
    return module_comp(path.relative_to(repo).parts)


def plain_import_anchor(dotted: Sequence[str], target_comp: PathParts) -> PathParts:
    """For `import a.b.mod` (no `as`), the bound name is "a", not the
    deeper "a.b.mod": drops the trailing components `dotted` has beyond
    the first, keeping whatever root prefix the real file layout adds
    ahead of the written dotted path."""
    drop = len(dotted) - 1
    return target_comp[: len(target_comp) - drop] if drop else target_comp


def plain_import_path_hops(
    dotted: Sequence[str], target_comp: PathParts
) -> frozenset[PathParts]:
    """Every intermediate dotted-prefix component a plain `import a.b.mod`
    walks through on its own -- `a`, `a.b`, `a.b.mod` -- so the attribute
    walk can skip them when it later re-derives the same hops from the
    bound name (`a.b.mod._x`): they're steps of this one import, not a
    second, independent attribute access."""
    anchor_len = len(target_comp) - len(dotted)
    return frozenset(target_comp[: anchor_len + d] for d in range(1, len(dotted) + 1))


def _walk_bound_attribute_chain(
    base_comp: PathParts,
    attrs: tuple[str, ...],
    module_candidates: ModuleCandidates,
    skip_hops: AbstractSet[PathParts],
) -> tuple[tuple[PathParts, ...], tuple[tuple[PathParts, str], ...]]:
    """Follows `attrs` from a name already bound (by an import) to the
    module at `base_comp`, one dotted hop at a time. Each hop that itself
    resolves to a module in the repo (a submodule or subpackage) is a
    module reference -- `import a; a._internal.x.f` reaches the private
    module `a._internal`, the same privacy rule an import statement
    triggers -- unless that hop lies on a plain `import a.b.c`'s own
    dotted path (`skip_hops`), in which case the walk still follows it but
    doesn't report it, since it's a step of that one import, not a second
    attribute access. The walk stops at the first hop that is not itself a
    module: that's a name access on the module reached so far (`mod._x`),
    checked the same way `from mod import _x` is. Anything past that first
    non-module hop (`.x.f` above) is out of scope -- an attribute of
    whatever value `_x` holds, not of a module this tool can resolve."""
    module_hops: list[PathParts] = []
    current = base_comp
    for attr in attrs:
        candidate = current + (attr,)
        if candidate in module_candidates:
            if candidate not in skip_hops:
                module_hops.append(candidate)
            current = candidate
            continue
        return tuple(module_hops), ((current, attr),)
    return tuple(module_hops), ()


def collect_attribute_accesses(
    tree: ast.Module,
    binding: Mapping[str, AbstractSet[PathParts]],
    module_candidates: ModuleCandidates,
    plain_import_hops: AbstractSet[PathParts],
) -> tuple[tuple[PathParts, ...], tuple[tuple[PathParts, str], ...]]:
    """Every module reached, and every name accessed, through an attribute
    chain rooted in a name `binding` maps to a module -- across every
    `ast.Attribute` node in the file. A name bound to several modules at
    once (two package roots each contributing a same-named top package,
    `import a.b; import a.c`) is walked from each anchor in turn. A
    chain's root not being a plain `ast.Name` (a call or subscript result)
    or not being in `binding` (an instance attribute like `self._y`, a
    base name bound to a third-party or unresolvable module) is silently
    skipped, not an error -- these are the rule's named blind spots. A
    name the file also rebinds locally (a parameter, assignment target, a
    for/with/except/comprehension target, a nested def/class) is dropped
    from `binding` entirely by the caller, so it's silently skipped here
    too, conservatively: a false finding becomes a missed one."""
    module_hops: list[PathParts] = []
    name_accesses: list[tuple[PathParts, str]] = []
    if not binding:
        return (), ()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        attrs: list[str] = []
        cur: ast.expr = node
        while isinstance(cur, ast.Attribute):
            attrs.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            continue
        base_comps = binding.get(cur.id)
        if not base_comps:
            continue
        for base_comp in base_comps:
            hops, accesses = _walk_bound_attribute_chain(
                base_comp, tuple(reversed(attrs)), module_candidates, plain_import_hops
            )
            module_hops.extend(hops)
            name_accesses.extend(accesses)
    return tuple(module_hops), tuple(name_accesses)


def locally_bound_names(tree: ast.Module) -> frozenset[str]:
    """Every name this file binds locally anywhere in its body: an `ast.arg`
    of any kind (positional, keyword-only, `*args`, `**kwargs`, lambda
    included), an `ast.Name` in Store context (plain assignment, tuple
    unpacking, `for`/`with`/`except`/comprehension targets -- all of these
    are `ast.Name` nodes with Store context), and `def`/`class` names
    (not `ast.Name` nodes, so listed separately). `global`/`nonlocal`
    declarations don't themselves bind a name, so they're not needed.
    Conservative on purpose: a name the file rebinds anywhere is dropped
    from `binding` for the whole file, not just after the rebinding point,
    so a false finding becomes a missed one -- the right trade for a gate."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return frozenset(names)
