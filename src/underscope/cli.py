"""underscope: see and check a repo's real Python imports.

Build one whole-repo Python import graph with ast (stdlib only) and
render it as mermaid views, one per directory, with a markdown link list
to zoom in/out. Nodes are file paths (or directory paths for namespace
packages without __init__.py); edge labels count distinct (importer file,
target file) pairs.
A non-ancestor candidate's root must lack __init__.py only when the
candidate itself is a single file (package candidates skip that filter).
Test directories (tests/test) and test files (test_*.py, *_test.py,
conftest.py) are skipped by default, no flag. `import a.b.c` must match
exactly; `from a.b import c` tries submodule `a.b.c` then falls back to
`a.b` exactly, never shorter. Each view opens with a summary: cycles are
strongly connected components (size >= 2) among the view's inside children
on the view's own edges, outside (dashed) nodes excluded; the node table
lists one row per inside child. Both sections carry their own legend text
in the rendered output defining each count; see that legend for units and
for the SCC_FULL_EDGE_LIMIT threshold controlling full vs. collapsed
one-way-edge lists in Cycles.

WARNING, blind spot: the graph shows import coupling only, a lower bound on
logical coupling ("if A changes, must B?"). Coupling that bypasses Python
imports stays invisible: HTTP APIs, shared database schemas and tables,
config keys and env vars, message and file formats, string-keyed registries,
runtime sys.path edits. Two services with no edge here can still force each
other's edits. Never read a missing edge as independence.

Three subcommands:

`underscope graph <repo> --out DIR [--html]` writes the views under DIR.
`--html` also writes an index.html per view (mermaid diagram, clickable
directory nodes) alongside index.md; the browser needs network access to
load mermaid from a CDN.

`underscope check <repo>` runs two convention checks and prints findings to
stdout, exit 1 on any finding: (1) no import cycles, at every directory
level (any SCC with more than 1 node in any directory's view, computed the
same way the view's own Cycles section is, TYPE_CHECKING and
function-body imports counted, tests excluded as always; no baseline,
existing cycles fail); (2) a Go-`internal/`-style privacy convention,
applied to both modules and names (Rust's visibility rule on PEP 8
underscores): a private item is visible in the thing that defines it and
that thing's descendants, never in its parent or siblings. For modules: a
file whose path, relative to the nearest ancestor directory with a
pyproject.toml [project] table, has a component starting with a single `_`
(not a dunder) may only be imported by files under the directory holding
that component, repo-wide, across package boundaries. For names: `from m
import _x` (or `_x as y`, absolute or relative) where `_x` is not itself a
resolvable submodule of `m` (that case is the module rule above, not
double-counted here) -- if `m` resolves to a single file, `_x` is private
to that file and no other file may import it, including a sibling in the
same directory or that directory's own `__init__.py`; if `m` resolves to a
package (`__init__.py` or a namespace package directory), `_x` is private
to that directory and only files under it may import it. The attribute
form (`import a.b; a.b._x`) is out of scope here; ruff's SLF001 already
flags it.

`underscope metrics <repo>` prints a coupling report to stdout on the same
file graph (reach and score per file and per directory, interface width
per file and per directory, exposure share, entry files, misplaced files)
and always exits 0: it is a report, never a gate."""

import argparse
import ast
import html
import itertools
import os
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# dotted module path components, e.g. ("shop", "billing", "pricing")
PathParts = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModuleCandidate:
    """A resolvable module: its file/dir path, and whether it's a namespace
    package directory (True) or a single .py file / __init__.py (False)."""

    path: Path
    is_namespace_dir: bool


ModuleCandidates = dict[PathParts, ModuleCandidate]


@dataclass(frozen=True, slots=True)
class SuffixEntry:
    """A module candidate indexed by dotted suffix."""

    comp: PathParts
    path: Path
    is_namespace_dir: bool


SuffixIndex = dict[PathParts, list[SuffixEntry]]
# an ambiguous-import warning: the importing file and its candidate targets
AmbiguityWarning = tuple[Path, list[Path]]
# (importer_rel, target_rel) file-level import edge
EdgeKey = tuple[Path, Path]
EdgeNames = dict[EdgeKey, set[str]]
# a resolved absolute/relative import: target file, imported names (if any)
ResolvedImport = tuple[Path, frozenset[str]]
# (view node src, view node tgt)
PairKey = tuple[Path, Path]
PairEdges = dict[PairKey, set[EdgeKey]]
# (link text, relative link target) for the markdown "Links:" list
Link = tuple[str, str]


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    """A module candidate scored for disambiguation against the importer."""

    root_depth: int
    is_ancestor: bool
    path: Path
    root: PathParts
    is_namespace_dir: bool


SKIP_DIRS = {
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


def should_skip_dir(name: str) -> bool:
    return name in SKIP_DIRS or name.startswith(".")


def is_test_file(name: str) -> bool:
    return name == "conftest.py" or name.startswith("test_") or name.endswith("_test.py")


def find_py_files(repo: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        for fn in filenames:
            if fn.endswith(".py") and not is_test_file(fn):
                files.append(Path(dirpath) / fn)
    return files


def module_comp(parts: tuple[str, ...]) -> PathParts:
    if parts[-1] == "__init__.py":
        return parts[:-1]
    return parts[:-1] + (parts[-1][:-3],)


def build_index(
    repo: Path, py_files: list[Path]
) -> tuple[set[Path], ModuleCandidates, SuffixIndex]:
    dirs_with_py: set[Path] = set()
    for f in py_files:
        d = f.relative_to(repo).parent
        while True:
            dirs_with_py.add(d)
            if d == Path("."):
                break
            d = d.parent

    module_candidates: ModuleCandidates = {}
    for f in py_files:
        rel = f.relative_to(repo)
        comp = module_comp(rel.parts)
        module_candidates[comp] = ModuleCandidate(f, False)

    for d in dirs_with_py:
        comp = () if d == Path(".") else d.parts
        if comp not in module_candidates:
            module_candidates[comp] = ModuleCandidate(
                repo / d if d != Path(".") else repo, True
            )

    suffix_index: SuffixIndex = {}
    for comp, cand in module_candidates.items():
        for k in range(1, len(comp) + 1):
            suf = comp[-k:]
            suffix_index.setdefault(suf, []).append(
                SuffixEntry(comp, cand.path, cand.is_namespace_dir)
            )

    return dirs_with_py, module_candidates, suffix_index


def path_components(p: Path) -> PathParts:
    return () if p == Path(".") else p.parts


def disambiguate(
    cands: list[SuffixEntry],
    importer_dir_parts: PathParts,
    suf: PathParts,
    importer_rel: Path,
    warnings: list[AmbiguityWarning],
    module_candidates: ModuleCandidates,
) -> Path | None:
    uniq: dict[tuple[PathParts, Path], bool] = {}
    for entry in cands:
        uniq[(entry.comp, entry.path)] = entry.is_namespace_dir
    entries = [SuffixEntry(comp, path, isd) for (comp, path), isd in uniq.items()]

    scored: list[ScoredCandidate] = []
    for entry in entries:
        root = entry.comp[: len(entry.comp) - len(suf)]
        is_ancestor = root == importer_dir_parts[: len(root)]
        scored.append(
            ScoredCandidate(
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
    def is_package(root: PathParts) -> bool:
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

    warnings.append((importer_rel, [s.path for s in non_package_root]))
    return None


def resolve_exact(
    dotted_parts: list[str],
    importer_dir_parts: PathParts,
    suffix_index: SuffixIndex,
    importer_rel: Path,
    warnings: list[AmbiguityWarning],
    module_candidates: ModuleCandidates,
) -> Path | None:
    if not dotted_parts or dotted_parts[0] in sys.stdlib_module_names:
        return None
    suf = tuple(dotted_parts)
    cands = suffix_index.get(suf)
    if not cands:
        return None
    return disambiguate(
        cands,
        importer_dir_parts,
        suf,
        importer_rel,
        warnings,
        module_candidates,
    )


def resolve_relative(
    module: str | None,
    level: int,
    names: list[ast.alias],
    importer_dir_parts: PathParts,
    module_candidates: ModuleCandidates,
) -> list[ResolvedImport]:
    drop = level - 1
    if drop > len(importer_dir_parts):
        return []
    target_dir = (
        importer_dir_parts[: len(importer_dir_parts) - drop]
        if drop
        else importer_dir_parts
    )

    results: list[ResolvedImport] = []
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
    return results


def top_level_defined_names(tree: ast.Module) -> set[str]:
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
    return defined


def collect_edges(
    repo: Path,
    py_files: list[Path],
    module_candidates: ModuleCandidates,
    suffix_index: SuffixIndex,
) -> tuple[EdgeNames, list[AmbiguityWarning], dict[Path, set[str]]]:
    # (importer_rel, target_rel) -> set of imported names (empty = no names,
    # e.g. a plain "import x.y" or a "from a import b" where b is itself the
    # resolved submodule rather than a name defined inside it)
    edge_names: EdgeNames = {}
    warnings: list[AmbiguityWarning] = []
    defined_names: dict[Path, set[str]] = {}
    for f in sorted(py_files):
        rel = f.relative_to(repo)
        try:
            text = f.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(f))
        except (SyntaxError, UnicodeDecodeError) as e:
            print(f"warning: skipping {rel}: {e}", file=sys.stderr)
            continue

        defined_names[rel] = top_level_defined_names(tree)
        importer_dir_parts = path_components(rel.parent)
        targets: list[ResolvedImport] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    dotted = alias.name.split(".")
                    t = resolve_exact(
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
                        resolve_relative(
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
                        t = resolve_exact(
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
                        t = resolve_exact(
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

    return edge_names, warnings, defined_names


def lca(a: Path, b: Path) -> Path:
    pa = () if a == Path(".") else a.parts
    pb = () if b == Path(".") else b.parts
    common: list[str] = []
    for x, y in zip(pa, pb):
        if x != y:
            break
        common.append(x)
    return Path(*common) if common else Path(".")


def child_of(directory: Path, path: Path) -> Path | None:
    if path == directory:
        return None
    if directory == Path("."):
        return Path(path.parts[0])
    rel = path.relative_to(directory)
    return directory / rel.parts[0]


def outside_node(directory: Path, path: Path) -> Path | None:
    return child_of(lca(directory, path), path)


def build_view(directory: Path, edge_names: EdgeNames) -> PairEdges:
    # (node_src, node_tgt) -> set of (importer_rel, target_rel) file-level edges
    pair_edges: PairEdges = {}
    for f_rel, t_rel in edge_names:
        f_in = f_rel.is_relative_to(directory)
        t_in = t_rel.is_relative_to(directory)
        if not f_in and not t_in:
            continue
        if f_in and t_in:
            src = child_of(directory, f_rel)
            tgt = child_of(directory, t_rel)
        elif f_in:
            src = child_of(directory, f_rel)
            tgt = outside_node(directory, t_rel)
        else:
            src = outside_node(directory, f_rel)
            tgt = child_of(directory, t_rel)
        if src is None or tgt is None or src == tgt:
            continue
        pair_edges.setdefault((src, tgt), set()).add((f_rel, t_rel))
    return pair_edges


def mermaid_id(path: Path) -> str:
    s = str(path).replace("\\", "/")
    return "n_" + "".join(c if c.isalnum() else "_" for c in s)


def view_index_path(out_dir: Path, directory: Path, name: str = "index.md") -> Path:
    if directory == Path("."):
        return out_dir / name
    return out_dir / directory / name


@dataclass(frozen=True, slots=True)
class MutualPair:
    """Two SCC members with edges in both directions, and each direction's
    distinct (importer file, target file) pair count."""

    a: Path
    b: Path
    a_to_b_files: int
    b_to_a_files: int


@dataclass(frozen=True, slots=True)
class SccEdge:
    """One directed edge inside a strongly connected component."""

    src: Path
    tgt: Path
    files: int


@dataclass(frozen=True, slots=True)
class Scc:
    """A strongly connected component (size >= 2) among a view's inside
    children, on the edges the view draws."""

    members: list[Path]
    mutual_pairs: list[MutualPair]
    edges: list[SccEdge]


@dataclass(frozen=True, slots=True)
class NodeRow:
    """One inside child's row in a view's summary node table."""

    node: Path
    importing_units: int
    distinct_names: int
    top_names: list[tuple[str, int]]
    internal: int
    outgoing: int
    root: int | None
    internals: int | None
    private: int
    reach_a: int
    reach_b: int
    score: int


def tarjan_sccs(nodes: set[Path], adj: dict[Path, set[Path]]) -> list[list[Path]]:
    """Iterative Tarjan (explicit stack, no recursion) so large views can't
    hit the recursion limit."""
    counter = 0
    index: dict[Path, int] = {}
    lowlink: dict[Path, int] = {}
    on_stack: dict[Path, bool] = {}
    stack: list[Path] = []
    sccs: list[list[Path]] = []

    for start in sorted(nodes, key=str):
        if start in index:
            continue
        work: list[tuple[Path, list[Path], int]] = [
            (start, sorted(adj.get(start, ()), key=str), 0)
        ]
        index[start] = lowlink[start] = counter
        counter += 1
        stack.append(start)
        on_stack[start] = True

        while work:
            v, children, pos = work[-1]
            advanced = False
            while pos < len(children):
                w = children[pos]
                pos += 1
                if w not in index:
                    index[w] = lowlink[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack[w] = True
                    work[-1] = (v, children, pos)
                    work.append((w, sorted(adj.get(w, ()), key=str), 0))
                    advanced = True
                    break
                if on_stack.get(w, False):
                    lowlink[v] = min(lowlink[v], index[w])
            if advanced:
                continue
            work[-1] = (v, children, pos)
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[v])
            if lowlink[v] == index[v]:
                comp: list[Path] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                sccs.append(comp)
    return sccs


def compute_sccs(inside_nodes: set[Path], pair_edges: PairEdges) -> list[Scc]:
    adj: dict[Path, set[Path]] = {n: set() for n in inside_nodes}
    file_counts: dict[EdgeKey, int] = {}
    for (src, tgt), file_pairs in pair_edges.items():
        if src in inside_nodes and tgt in inside_nodes:
            adj[src].add(tgt)
            file_counts[(src, tgt)] = len(file_pairs)

    raw = [comp for comp in tarjan_sccs(inside_nodes, adj) if len(comp) >= 2]

    result: list[Scc] = []
    for comp in raw:
        members = sorted(comp, key=str)
        member_set = set(comp)
        mutual_pairs: list[MutualPair] = []
        seen: set[frozenset[Path]] = set()
        for a in members:
            for b in members:
                if a == b:
                    continue
                if (a, b) in file_counts and (b, a) in file_counts:
                    key = frozenset((a, b))
                    if key in seen:
                        continue
                    seen.add(key)
                    mutual_pairs.append(
                        MutualPair(a, b, file_counts[(a, b)], file_counts[(b, a)])
                    )
        mutual_pairs.sort(key=lambda mp: (str(mp.a), str(mp.b)))
        edges = sorted(
            (
                SccEdge(s, t, c)
                for (s, t), c in file_counts.items()
                if s in member_set and t in member_set
            ),
            key=lambda e: (str(e.src), str(e.tgt)),
        )
        result.append(Scc(members, mutual_pairs, edges))

    result.sort(key=lambda scc: (-len(scc.members), str(scc.members[0])))
    return result


def is_private_part(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def has_project_table(pyproject: Path) -> bool:
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return "project" in data


def find_package_roots(repo: Path) -> list[Path]:
    """Directories (relative to repo) whose pyproject.toml has a [project]
    table, used only to anchor the privacy rule's "package root". Sorted
    deepest-first so the closest ancestor is found first."""
    roots: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        if "pyproject.toml" in filenames and has_project_table(
            Path(dirpath) / "pyproject.toml"
        ):
            roots.append(Path(dirpath).relative_to(repo))
    return sorted(roots, key=lambda p: len(p.parts), reverse=True)


def owning_package_root(rel: Path, package_roots: list[Path]) -> Path:
    for root in package_roots:
        if root != Path(".") and rel.is_relative_to(root):
            return root
    return Path(".")


def private_owner_dir(rel: Path, package_roots: list[Path]) -> Path | None:
    """The directory a private file/dir component makes it private to, or
    None if no component of rel (relative to its package root) is private."""
    root = owning_package_root(rel, package_roots)
    sub = rel.relative_to(root) if root != Path(".") else rel
    comp = module_comp(sub.parts)
    for k, part in enumerate(comp):
        if is_private_part(part):
            return root / Path(*comp[:k]) if comp[:k] else root
    return None


@dataclass(frozen=True, slots=True)
class PrivacyViolation:
    """An import that reaches a module private to a directory it is not
    under."""

    importer: Path
    target: Path
    owner: Path


def find_privacy_violations(
    edge_names: EdgeNames, package_roots: list[Path]
) -> list[PrivacyViolation]:
    violations: list[PrivacyViolation] = []
    owner_cache: dict[Path, Path | None] = {}

    def owner_of(rel: Path) -> Path | None:
        if rel not in owner_cache:
            owner_cache[rel] = private_owner_dir(rel, package_roots)
        return owner_cache[rel]

    for importer_rel, target_rel in edge_names:
        owner = owner_of(target_rel)
        if owner is None or importer_rel.is_relative_to(owner):
            continue
        violations.append(PrivacyViolation(importer_rel, target_rel, owner))

    violations.sort(key=lambda v: (str(v.importer), str(v.target)))
    return violations


def is_private_name(name: str) -> bool:
    """A name private by Rust's convention, ported to PEP 8 underscores:
    one leading underscore, not a dunder, not the bare `_` placeholder
    name."""
    return name.startswith("_") and not name.startswith("__") and name != "_"


def name_owner(target_rel: Path) -> tuple[Path, bool]:
    """Where a name imported `from target_rel import _x` is private to:
    (owner, owner_is_dir). A package (`__init__.py` file, or a namespace
    package directory with no `__init__.py`) makes its names visible to
    files under that directory; a single module file makes its names
    visible to no other file at all."""
    if target_rel.name == "__init__.py":
        return target_rel.parent, True
    if target_rel.suffix != ".py":
        return target_rel, True
    return target_rel, False


@dataclass(frozen=True, slots=True)
class NameViolation:
    """An import of a private name (`from m import _x`) that reaches
    outside the directory (package) or file that name is private to."""

    importer: Path
    target: Path
    name: str
    owner: Path
    owner_is_dir: bool


def find_name_violations(edge_names: EdgeNames) -> list[NameViolation]:
    violations: list[NameViolation] = []
    for (importer_rel, target_rel), names in edge_names.items():
        for name in names:
            if not is_private_name(name):
                continue
            owner, owner_is_dir = name_owner(target_rel)
            if owner_is_dir and importer_rel.is_relative_to(owner):
                continue
            violations.append(
                NameViolation(importer_rel, target_rel, name, owner, owner_is_dir)
            )
    violations.sort(key=lambda v: (str(v.importer), str(v.target), v.name))
    return violations


@dataclass(frozen=True, slots=True)
class CycleFinding:
    """One SCC (size >= 2) found in one directory's view, with a concrete
    example loop through file-level edges."""

    directory: Path
    members: list[Path]
    example: list[Path]
    example_files: list[tuple[Path, Path]]


def find_example_cycle(members: list[Path], pair_edges: PairEdges) -> list[Path]:
    """One concrete node loop (first == last) through the SCC, via DFS."""
    member_set = set(members)
    adj: dict[Path, list[Path]] = {m: [] for m in members}
    for src, tgt in pair_edges:
        if src in member_set and tgt in member_set and src != tgt:
            adj[src].append(tgt)
    for neighbors in adj.values():
        neighbors.sort(key=str)

    start = members[0]
    stack: list[Path] = [start]
    visited: set[Path] = {start}

    def dfs(node: Path) -> list[Path] | None:
        for nxt in adj[node]:
            if nxt == start and len(stack) > 1:
                return [*stack, start]
            if nxt not in visited:
                visited.add(nxt)
                stack.append(nxt)
                result = dfs(nxt)
                if result is not None:
                    return result
                stack.pop()
        return None

    return dfs(start) or [start]


def example_file_edges(
    example: list[Path], pair_edges: PairEdges
) -> list[tuple[Path, Path]]:
    out: list[tuple[Path, Path]] = []
    for a, b in itertools.pairwise(example):
        file_pairs = pair_edges.get((a, b))
        if file_pairs:
            out.append(min(file_pairs, key=lambda p: (str(p[0]), str(p[1]))))
    return out


def compute_inside_nodes(directory: Path, pair_edges: PairEdges) -> set[Path]:
    nodes: set[Path] = set()
    for src, tgt in pair_edges:
        nodes.add(src)
        nodes.add(tgt)
    outside = {n for n in nodes if not n.is_relative_to(directory)}
    return nodes - outside


def find_cycle_findings(
    directory: Path, inside_nodes: set[Path], pair_edges: PairEdges
) -> list[CycleFinding]:
    findings: list[CycleFinding] = []
    for scc in compute_sccs(inside_nodes, pair_edges):
        example = find_example_cycle(scc.members, pair_edges)
        findings.append(
            CycleFinding(
                directory, scc.members, example, example_file_edges(example, pair_edges)
            )
        )
    return findings


def count_file_level_cycles(
    py_files: list[Path], repo: Path, edge_names: EdgeNames
) -> int:
    """Distinct cycles on the raw file-level graph, ignoring any directory
    view's aggregation -- the number of genuinely separate root causes."""
    nodes = {f.relative_to(repo) for f in py_files}
    adj: dict[Path, set[Path]] = {n: set() for n in nodes}
    for f_rel, t_rel in edge_names:
        if f_rel in adj and t_rel in adj:
            adj[f_rel].add(t_rel)
    sccs = tarjan_sccs(nodes, adj)
    return sum(1 for comp in sccs if len(comp) >= 2)


def build_file_graph(
    nodes: set[Path], edge_names: EdgeNames
) -> tuple[dict[Path, set[Path]], dict[Path, set[Path]]]:
    """The raw file-level import graph (forward adjacency and its reverse),
    same edges `--check`'s cycle rule uses: no self-edges (collect_edges
    already drops those), duplicates already collapsed by edge_names'
    dict keys."""
    adj: dict[Path, set[Path]] = {n: set() for n in nodes}
    radj: dict[Path, set[Path]] = {n: set() for n in nodes}
    for importer_rel, target_rel in edge_names:
        if importer_rel in adj and target_rel in adj and importer_rel != target_rel:
            adj[importer_rel].add(target_rel)
            radj[target_rel].add(importer_rel)
    return adj, radj


def reachable_set(start: Path, adj: dict[Path, set[Path]]) -> set[Path]:
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
    return seen


def compute_reach(
    nodes: set[Path], adj: dict[Path, set[Path]], radj: dict[Path, set[Path]]
) -> tuple[dict[Path, set[Path]], dict[Path, set[Path]]]:
    """Per file: the set of dependents (other files with an import path to
    it) and the set of dependencies (other files it has an import path
    to), both transitive."""
    dependents = {n: reachable_set(n, radj) for n in nodes}
    dependencies = {n: reachable_set(n, adj) for n in nodes}
    return dependents, dependencies


def top_n_by_count(counts: dict[Path, int], n: int = 10) -> list[tuple[Path, int]]:
    return sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:n]


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


def compute_file_scores(
    nodes: set[Path],
    dependents: dict[Path, set[Path]],
    dependencies: dict[Path, set[Path]],
) -> dict[Path, FileScore]:
    scores: dict[Path, FileScore] = {}
    for n in nodes:
        d_in = len(dependents[n])
        d_out = len(dependencies[n])
        scores[n] = FileScore(n, d_in, d_out, d_in * d_out)
    return scores


def compute_dir_scores(
    dirs_with_py: set[Path],
    dependents: dict[Path, set[Path]],
    dependencies: dict[Path, set[Path]],
) -> dict[Path, DirScore]:
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


@dataclass(frozen=True, slots=True)
class FileWidth:
    """A target file's interface width: distinct names other files import
    from it, and distinct files importing any of those names."""

    target: Path
    names: int
    importers: int


def compute_file_widths(nodes: set[Path], edge_names: EdgeNames) -> list[FileWidth]:
    names_by_target: dict[Path, set[str]] = {}
    bare_targets: set[Path] = set()
    importers_by_target: dict[Path, set[Path]] = {}
    for (importer_rel, target_rel), names in edge_names.items():
        if importer_rel == target_rel or target_rel not in nodes:
            continue
        importers_by_target.setdefault(target_rel, set()).add(importer_rel)
        if names:
            names_by_target.setdefault(target_rel, set()).update(names)
        else:
            bare_targets.add(target_rel)

    widths: list[FileWidth] = []
    for target in nodes:
        name_count = len(names_by_target.get(target, set())) + (
            1 if target in bare_targets else 0
        )
        importer_count = len(importers_by_target.get(target, set()))
        widths.append(FileWidth(target, name_count, importer_count))
    return widths


def ancestor_dirs(rel: Path) -> list[Path]:
    """The directory holding `rel` and every ancestor directory up to the
    repo root ('.'), inclusive."""
    out = [rel.parent]
    while out[-1] != Path("."):
        out.append(out[-1].parent)
    return out


def compute_wrapper_dirs(nodes: set[Path], dirs_with_py: set[Path]) -> set[Path]:
    """Directories that hold no .py file of their own and have exactly one
    child directory with Python code, so their file set is identical to
    that child's (and, up a wrapper chain, to the file set of every other
    wrapper in the chain). `--metrics`'s directory tables skip these,
    keeping only the deepest directory of each such chain."""
    direct_file_count: dict[Path, int] = {}
    for f in nodes:
        d = f.parent
        direct_file_count[d] = direct_file_count.get(d, 0) + 1

    children: dict[Path, list[Path]] = {}
    for d in dirs_with_py:
        if d == Path("."):
            continue
        children.setdefault(d.parent, []).append(d)

    return {
        d
        for d in dirs_with_py
        if direct_file_count.get(d, 0) == 0 and len(children.get(d, [])) == 1
    }


@dataclass(frozen=True, slots=True)
class DirWidth:
    """A directory's interface width: distinct (target file, name) pairs
    files outside it import from files inside it, and distinct outside
    importing files."""

    directory: Path
    names: int
    importers: int


def compute_dir_widths(dirs_with_py: set[Path], edge_names: EdgeNames) -> list[DirWidth]:
    named_pairs: dict[Path, set[tuple[Path, str]]] = {}
    bare_targets: dict[Path, set[Path]] = {}
    importers: dict[Path, set[Path]] = {}
    for (importer_rel, target_rel), names in edge_names.items():
        for directory in ancestor_dirs(target_rel):
            if directory not in dirs_with_py:
                continue
            if importer_rel.is_relative_to(directory):
                continue
            importers.setdefault(directory, set()).add(importer_rel)
            if names:
                named_pairs.setdefault(directory, set()).update(
                    (target_rel, name) for name in names
                )
            else:
                bare_targets.setdefault(directory, set()).add(target_rel)

    widths: list[DirWidth] = []
    for directory in dirs_with_py:
        name_count = len(named_pairs.get(directory, set())) + len(
            bare_targets.get(directory, set())
        )
        importer_count = len(importers.get(directory, set()))
        widths.append(DirWidth(directory, name_count, importer_count))
    return widths


@dataclass(frozen=True, slots=True)
class Exposure:
    """A file's exposure share: how many of its top-level defined names
    (def, async def, class, plain/annotated assignment to a bare name;
    imports don't count) at least one other file actually imports."""

    path: Path
    used: int
    defined: int
    share: float
    importers: int


def compute_exposure(
    nodes: set[Path], edge_names: EdgeNames, defined_names: dict[Path, set[str]]
) -> dict[Path, Exposure]:
    imported_names: dict[Path, set[str]] = {}
    importers_by_name: dict[Path, dict[str, set[Path]]] = {}
    for (importer_rel, target_rel), names in edge_names.items():
        if not names or importer_rel == target_rel:
            continue
        imported_names.setdefault(target_rel, set()).update(names)
        per_name = importers_by_name.setdefault(target_rel, {})
        for name in names:
            per_name.setdefault(name, set()).add(importer_rel)

    exposures: dict[Path, Exposure] = {}
    for f in nodes:
        if f.name == "__init__.py":
            continue
        defined = defined_names.get(f, set())
        used_names = defined & imported_names.get(f, set())
        per_name = importers_by_name.get(f, {})
        importer_set: set[Path] = set()
        for name in used_names:
            importer_set.update(per_name.get(name, set()))
        used = len(used_names)
        total = len(defined)
        share = used / total if total else 0.0
        exposures[f] = Exposure(f, used, total, share, len(importer_set))
    return exposures


@dataclass(frozen=True, slots=True)
class EntryFiles:
    """A directory's entry-file share: how many of its files at least one
    file outside the directory imports directly, out of all files inside
    it; outside_importers is the distinct outside files doing so."""

    directory: Path
    entry: int
    files: int
    share: float
    outside_importers: int


def compute_entry_files(
    dirs_with_py: set[Path], nodes: set[Path], edge_names: EdgeNames
) -> dict[Path, EntryFiles]:
    entry_by_dir: dict[Path, set[Path]] = {}
    outside_importers_by_dir: dict[Path, set[Path]] = {}
    for importer_rel, target_rel in edge_names:
        for directory in ancestor_dirs(target_rel):
            if directory not in dirs_with_py:
                continue
            if importer_rel.is_relative_to(directory):
                continue
            entry_by_dir.setdefault(directory, set()).add(target_rel)
            outside_importers_by_dir.setdefault(directory, set()).add(importer_rel)

    files_by_dir: dict[Path, int] = {}
    for f in nodes:
        for directory in ancestor_dirs(f):
            if directory in dirs_with_py:
                files_by_dir[directory] = files_by_dir.get(directory, 0) + 1

    out: dict[Path, EntryFiles] = {}
    for directory in dirs_with_py:
        files = files_by_dir.get(directory, 0)
        entry = len(entry_by_dir.get(directory, set()))
        share = entry / files if files else 0.0
        outside = len(outside_importers_by_dir.get(directory, set()))
        out[directory] = EntryFiles(directory, entry, files, share, outside)
    return out


@dataclass(frozen=True, slots=True)
class Misplaced:
    """A file whose direct import partners (files it imports plus files
    importing it, from the same file graph the scores use) cluster more
    heavily in some other directory than in its own."""

    path: Path
    own: int
    other: int
    other_dir: Path
    imported_by: int
    imports: int


def compute_misplaced(
    nodes: set[Path],
    adj: dict[Path, set[Path]],
    radj: dict[Path, set[Path]],
) -> list[Misplaced]:
    results: list[Misplaced] = []
    for f in nodes:
        importers = radj.get(f, set()) - {f}
        imports_of = adj.get(f, set()) - {f}
        partners = importers | imports_of
        own_dir = f.parent
        own = 0
        other_counts: dict[Path, int] = {}
        for p in partners:
            d = p.parent
            if d == own_dir:
                own += 1
            else:
                other_counts[d] = other_counts.get(d, 0) + 1
        other = 0
        other_dir = Path(".")
        if other_counts:
            best = max(other_counts.values())
            # tie-break deterministically: smallest path among the ties
            other_dir = min(d for d, c in other_counts.items() if c == best)
            other = best
        if other >= 3 and other > own:
            imported_by = sum(1 for p in importers if p.parent == other_dir)
            imports = sum(1 for p in imports_of if p.parent == other_dir)
            results.append(Misplaced(f, own, other, other_dir, imported_by, imports))
    return results


def render_metrics(
    repo: Path,
    py_files: list[Path],
    dirs_with_py: set[Path],
    edge_names: EdgeNames,
    file_scores: dict[Path, FileScore],
    dir_scores: dict[Path, DirScore],
    defined_names: dict[Path, set[str]],
    adj: dict[Path, set[Path]],
    radj: dict[Path, set[Path]],
) -> list[str]:
    nodes = {f.relative_to(repo) for f in py_files}
    n = len(nodes)
    dependents = {p: fs.dependents for p, fs in file_scores.items()}
    dependencies = {p: fs.dependencies for p, fs in file_scores.items()}
    wrapper_dirs = compute_wrapper_dirs(nodes, dirs_with_py)

    lines: list[str] = ["# Coupling report", ""]
    lines.append("## Reach")
    lines.append("")
    lines.append(
        "dependents(f): other files with an import path to f, direct or "
        + "transitive; dependencies(f): other files f has an import path "
        + "to; score(f) = dependents(f) x dependencies(f)."
    )
    lines.append("")
    lines.append(f"- N (non-test .py files): {n}")
    lines.append(f"- file-level edges: {len(edge_names)}")
    lines.append("")

    lines.append("Top 10 by dependents:")
    lines.append("")
    for path, count in top_n_by_count(dependents):
        share = count / n * 100 if n else 0.0
        lines.append(f"- {path}: {count} ({share:.1f}% of N)")
    lines.append("")

    lines.append("Top 10 by dependencies:")
    lines.append("")
    for path, count in top_n_by_count(dependencies):
        share = count / n * 100 if n else 0.0
        lines.append(f"- {path}: {count} ({share:.1f}% of N)")
    lines.append("")

    lines.append(
        "All files by score (dependents x dependencies), ties: dependents "
        + "desc, then path:"
    )
    lines.append("")
    lines.append("| file | dependents | dependencies | score |")
    lines.append("| --- | --- | --- | --- |")
    for fs in sorted(
        file_scores.values(), key=lambda s: (-s.score, -s.dependents, str(s.path))
    ):
        lines.append(f"| {fs.path} | {fs.dependents} | {fs.dependencies} | {fs.score} |")
    lines.append("")

    lines.append(
        "All directories by score (in x out, measured across the "
        + "directory's own boundary, repo-wide); a directory whose file "
        + "set is identical to its one child directory's is skipped, "
        + "keeping only the deepest directory of such a wrapper chain:"
    )
    lines.append("")
    lines.append("| directory | in | out | score |")
    lines.append("| --- | --- | --- | --- |")
    for ds in sorted(
        (s for s in dir_scores.values() if s.path not in wrapper_dirs),
        key=lambda s: (-s.score, str(s.path)),
    ):
        directory_label = "repo root" if ds.path == Path(".") else str(ds.path)
        lines.append(
            f"| {directory_label} | {ds.reach_in} | {ds.reach_out} | {ds.score} |"
        )
    lines.append("")

    lines.append("## Interface width")
    lines.append("")
    lines.append(
        "distinct names other files import from a file or directory "
        + "(a nameless import, e.g. `import x`, counts the target as 1 "
        + "name), and the distinct files importing any of them."
    )
    lines.append("")

    file_widths = sorted(
        compute_file_widths(nodes, edge_names),
        key=lambda w: (-w.names, str(w.target)),
    )[:10]
    lines.append("Top 10 files by name count:")
    lines.append("")
    for w in file_widths:
        lines.append(f"- {w.target}: {w.names} names, {w.importers} importers")
    lines.append("")

    dir_widths = sorted(
        (
            w
            for w in compute_dir_widths(dirs_with_py, edge_names)
            if w.directory not in wrapper_dirs
        ),
        key=lambda w: (-w.names, str(w.directory)),
    )[:10]
    lines.append(
        "Top 10 directories by name count (wrapper directories, see " + "above, skipped):"
    )
    lines.append("")
    for w in dir_widths:
        directory_label = "repo root" if w.directory == Path(".") else str(w.directory)
        lines.append(f"- {directory_label}: {w.names} names, {w.importers} importers")
    lines.append("")

    lines.append("## Exposure share")
    lines.append("")
    lines.append(
        "Per file (`__init__.py` excluded): defined = top-level names it "
        + "defines (def, async def, class, plain/annotated assignment to a "
        + "bare name; imports don't count); used = those names at least one "
        + "other file imports; share = used / defined. Files with defined "
        + ">= 10, sorted by share desc, then defined desc."
    )
    lines.append("")
    exposures = [
        e
        for e in compute_exposure(nodes, edge_names, defined_names).values()
        if e.defined >= 10
    ]
    exposures.sort(key=lambda e: (-e.share, -e.defined, str(e.path)))
    lines.append("| file | used | defined | share | importing files |")
    lines.append("| --- | --- | --- | --- | --- |")
    for e in exposures:
        lines.append(
            f"| {e.path} | {e.used} | {e.defined} | {e.share * 100:.1f}% "
            + f"| {e.importers} |"
        )
    lines.append("")

    lines.append("## Entry files")
    lines.append("")
    lines.append(
        "Per directory (directories with >= 5 files): entry files = files "
        + "inside the directory at least one file outside it imports "
        + "directly; share = entry files / files; outside importers = "
        + "distinct outside files importing anything inside. Sorted by "
        + "share desc, then outside importers desc. Wrapper directories, "
        + "see above, skipped."
    )
    lines.append("")
    entries = [
        e
        for e in compute_entry_files(dirs_with_py, nodes, edge_names).values()
        if e.files >= 5 and e.directory not in wrapper_dirs
    ]
    entries.sort(key=lambda e: (-e.share, -e.outside_importers, str(e.directory)))
    lines.append("| directory | entry files | files | share | outside importers |")
    lines.append("| --- | --- | --- | --- | --- |")
    for e in entries:
        directory_label = "repo root" if e.directory == Path(".") else str(e.directory)
        lines.append(
            f"| {directory_label} | {e.entry} | {e.files} | {e.share * 100:.1f}% "
            + f"| {e.outside_importers} |"
        )
    lines.append("")

    lines.append("## Misplaced files")
    lines.append("")
    misplaced = sorted(
        compute_misplaced(nodes, adj, radj),
        key=lambda m: (-(m.other - m.own), str(m.path)),
    )
    lines.append(f"{len(misplaced)} of {n} files misplaced.")
    lines.append("")
    lines.append(
        "partners(f) = files f imports directly plus files importing f "
        + "directly; own = partners in f's own parent directory; other = "
        + "the largest count among all other parent directories, naming "
        + "that directory; misplaced when other >= 3 and other > own. "
        + "'imported by' = those other-directory partners that import f; "
        + "'imports' = those other-directory partners f imports; a "
        + "partner doing both counts in both, so the two may sum to more "
        + "than other. Sorted by other - own desc, then path."
    )
    lines.append("")
    lines.append("| file | own | other | imported by | imports | other directory |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for m in misplaced:
        other_dir_label = "repo root" if m.other_dir == Path(".") else str(m.other_dir)
        lines.append(
            f"| {m.path} | {m.own} | {m.other} | {m.imported_by} | {m.imports} "
            + f"| {other_dir_label} |"
        )
    lines.append("")

    return lines


def compute_node_rows(
    repo: Path,
    inside_nodes: set[Path],
    dirs_with_py: set[Path],
    pair_edges: PairEdges,
    edge_names: EdgeNames,
    file_scores: dict[Path, FileScore],
    dir_scores: dict[Path, DirScore],
) -> list[NodeRow]:
    rows: list[NodeRow] = []
    for node in inside_nodes:
        incoming_file_pairs: set[EdgeKey] = set()
        importing_units: set[Path] = set()
        name_units: dict[str, set[Path]] = {}
        distinct_target_names: set[tuple[Path, str]] = set()
        private = 0

        for (src, tgt), file_pairs in pair_edges.items():
            if tgt != node or src == node:
                continue
            importing_units.add(src)
            incoming_file_pairs |= file_pairs
            for f_rel, t_rel in file_pairs:
                for name in edge_names.get((f_rel, t_rel), ()):
                    distinct_target_names.add((t_rel, name))
                    name_units.setdefault(name, set()).add(src)
                    if name.startswith("_") and not name.startswith("__"):
                        private += 1

        top_names = sorted(name_units.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:5]
        top_names_fmt = [(name, len(units)) for name, units in top_names]

        internal_pairs = {
            (f_rel, t_rel)
            for (f_rel, t_rel) in edge_names
            if f_rel.is_relative_to(node) and t_rel.is_relative_to(node)
        }

        outgoing_pairs: set[EdgeKey] = set()
        for (src, tgt), file_pairs in pair_edges.items():
            if src == node and tgt != node:
                outgoing_pairs |= file_pairs

        root: int | None
        internals: int | None
        init_path = node / "__init__.py"
        if node in dirs_with_py and (repo / init_path).is_file():
            root = sum(1 for _, t_rel in incoming_file_pairs if t_rel == init_path)
            internals = len(incoming_file_pairs) - root
        else:
            root = None
            internals = None

        if node in dirs_with_py:
            ds = dir_scores[node]
            reach_a, reach_b, score = ds.reach_in, ds.reach_out, ds.score
        else:
            fs = file_scores[node]
            reach_a, reach_b, score = fs.dependents, fs.dependencies, fs.score

        rows.append(
            NodeRow(
                node,
                len(importing_units),
                len(distinct_target_names),
                top_names_fmt,
                len(internal_pairs),
                len(outgoing_pairs),
                root,
                internals,
                private,
                reach_a,
                reach_b,
                score,
            )
        )

    rows.sort(key=lambda r: (-r.importing_units, str(r.node)))
    return rows


# an SCC with at most this many internal edges (one per ordered (src, tgt)
# child pair, not file pairs) lists every edge; above it, only mutual pairs
# are listed plus a count of the rest (see Edges for those).
SCC_FULL_EDGE_LIMIT = 30


def render_summary(
    repo: Path,
    inside_nodes: set[Path],
    dirs_with_py: set[Path],
    pair_edges: PairEdges,
    edge_names: EdgeNames,
    label: Callable[[Path], str],
    file_scores: dict[Path, FileScore],
    dir_scores: dict[Path, DirScore],
) -> list[str]:
    lines: list[str] = ["## Cycles", ""]
    sccs = compute_sccs(inside_nodes, pair_edges)
    if not sccs:
        lines.append("no cycles")
    else:
        for scc in sccs:
            lines.append(f"- {', '.join(label(m) for m in scc.members)}")
            for mp in scc.mutual_pairs:
                pair_str = f"{label(mp.a)} <-> {label(mp.b)}"
                count_str = f"({mp.a_to_b_files}/{mp.b_to_a_files})"
                lines.append(f"  - {pair_str} {count_str}")
            mutual_members = {(mp.a, mp.b) for mp in scc.mutual_pairs} | {
                (mp.b, mp.a) for mp in scc.mutual_pairs
            }
            one_way = [e for e in scc.edges if (e.src, e.tgt) not in mutual_members]
            if not scc.mutual_pairs or len(scc.edges) <= SCC_FULL_EDGE_LIMIT:
                # A->B->C->A with no mutual pair: the one-way edges are the
                # cycle itself, so keep the full list instead of collapsing
                # it. Small SCCs (<= SCC_FULL_EDGE_LIMIT internal edges) also
                # list every one-way edge instead of collapsing to a count.
                for e in one_way:
                    lines.append(f"  - {label(e.src)} -> {label(e.tgt)} ({e.files})")
            elif one_way:
                lines.append(
                    f"  - {len(one_way)} more edges inside this cycle, see Edges."
                )
        lines.append("")
        lines.append(
            "`a <-> b (X/Y)` means X file pairs a->b, Y file pairs b->a "
            + "(distinct importer,target file pairs, same count as an Edges heading)."
        )
        lines.append(
            f"An SCC with at most {SCC_FULL_EDGE_LIMIT} internal edges (one "
            + "per ordered child pair) lists every one-way edge as "
            + "`a -> b (N)`; above that, the one-way edges shrink to a count, "
            + "see Edges, unless the SCC has no mutual pair."
        )
    lines.append("")

    lines.append("## Node summary")
    lines.append("")
    rows = compute_node_rows(
        repo,
        inside_nodes,
        dirs_with_py,
        pair_edges,
        edge_names,
        file_scores,
        dir_scores,
    )
    lines.append(
        "| node | importing units | names | internal | outgoing | root/internals "
        + "| private | dependents/in | dependencies/out | score |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in rows:
        names_cell = str(row.distinct_names)
        if row.top_names:
            top_str = ", ".join(f"{n} ({c})" for n, c in row.top_names)
            names_cell = f"{row.distinct_names} ({top_str})"
        root_cell = "-" if row.root is None else f"{row.root}/{row.internals}"
        left = f"| {label(row.node)} | {row.importing_units} | {names_cell} |"
        right = f" {row.internal} | {row.outgoing} | {root_cell} | {row.private} |"
        reach_cell = f" {row.reach_a} | {row.reach_b} | {row.score} |"
        lines.append(left + right + reach_cell)
    lines.append("")
    lines.append(
        "- importing units: distinct other view nodes with an edge into this node"
    )
    lines.append(
        "- names: distinct (target file, name) pairs imported from this node, "
        + "then the top 5 names by importing-unit count"
    )
    lines.append(
        "- internal: distinct (importer file, target file) pairs with both "
        + "files inside this node"
    )
    lines.append(
        "- outgoing: distinct (importer file, target file) pairs from this "
        + "node to other view nodes"
    )
    lines.append(
        "- root/internals: incoming file pairs into this node's own "
        + '__init__.py vs into its deeper files ("-" for a single file or a '
        + "namespace dir without __init__.py)"
    )
    lines.append(
        "- private: (importer, target, name) triples from other nodes where "
        + 'name starts with a single "_" (not dunder)'
    )
    lines.append(
        "- dependents/in, dependencies/out, score: computed once repo-wide "
        + "(not from this view's edges); for a file node, transitive "
        + "dependents and dependencies and their product; for a directory "
        + "node, files outside it that reach in/are reached out of it "
        + "transitively, and their product"
    )
    lines.append("")
    return lines


MERMAID_CDN_URL = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"
# headroom above mermaid's defaults (maxTextSize 50000, maxEdges 500): the
# largest view seen in practice had a ~38000-char mermaid block with ~400
# edges, so double both defaults.
MERMAID_MAX_TEXT_SIZE = 100_000
MERMAID_MAX_EDGES = 1_000


def build_mermaid_lines(
    nodes: set[Path],
    ids: dict[Path, str],
    outside_nodes: set[Path],
    label: Callable[[Path], str],
    ordered_pairs: list[tuple[PairKey, set[EdgeKey]]],
) -> list[str]:
    lines: list[str] = ["flowchart LR"]
    lines.append("    classDef outside fill:#eee,stroke:#999,stroke-dasharray: 5 5;")
    for n in sorted(nodes, key=str):
        lines.append(f'    {ids[n]}["{label(n)}"]')
    for (src, tgt), file_pairs in ordered_pairs:
        lines.append(f'    {ids[src]} -->|"{len(file_pairs)}"| {ids[tgt]}')
    if outside_nodes:
        lines.append(
            "    class " + ",".join(sorted(ids[n] for n in outside_nodes)) + " outside;"
        )
    return lines


def write_html_view(
    out_dir: Path,
    directory: Path,
    mermaid_lines: list[str],
    nodes: set[Path],
    dirs_with_py: set[Path],
) -> None:
    html_path = view_index_path(out_dir, directory, "index.html")
    title = "repo root" if directory == Path(".") else str(directory)

    body_lines = list(mermaid_lines)
    for n in sorted(nodes, key=str):
        if n in dirs_with_py:
            target_html = view_index_path(out_dir, n, "index.html")
            rel_link = os.path.relpath(target_html, start=html_path.parent)
            body_lines.append(f'    click {mermaid_id(n)} "{rel_link}"')
    mermaid_src = html.escape("\n".join(body_lines))

    parent_link_html = ""
    if directory != Path("."):
        parent_index = view_index_path(out_dir, directory.parent, "index.html")
        rel_link = os.path.relpath(parent_index, start=html_path.parent)
        parent_link_html = f'<p><a href="{rel_link}">parent view</a></p>\n'

    page = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>import graph: {html.escape(title)}</title>
<style>
pre.mermaid {{ overflow: auto; white-space: pre; }}
pre.mermaid svg {{ max-width: none !important; }}
</style>
</head>
<body>
<h1>import graph: {html.escape(title)}</h1>
{parent_link_html}<pre class="mermaid">
{mermaid_src}
</pre>
<script type="module">
import mermaid from "{MERMAID_CDN_URL}";
mermaid.initialize({{
  startOnLoad: true,
  securityLevel: "loose",
  maxTextSize: {MERMAID_MAX_TEXT_SIZE},
  maxEdges: {MERMAID_MAX_EDGES},
  flowchart: {{ useMaxWidth: false }}
}});
</script>
</body>
</html>
"""
    html_path.write_text(page, encoding="utf-8")


def write_view(
    out_dir: Path,
    repo: Path,
    directory: Path,
    dirs_with_py: set[Path],
    pair_edges: PairEdges,
    edge_names: EdgeNames,
    write_html: bool,
    file_scores: dict[Path, FileScore],
    dir_scores: dict[Path, DirScore],
) -> None:
    index_path = view_index_path(out_dir, directory)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    nodes: set[Path] = set()
    for src, tgt in pair_edges:
        nodes.add(src)
        nodes.add(tgt)

    ids = {n: mermaid_id(n) for n in nodes}
    outside_nodes: set[Path] = set()
    for src, tgt in pair_edges:
        if not src.is_relative_to(directory):
            outside_nodes.add(src)
        if not tgt.is_relative_to(directory):
            outside_nodes.add(tgt)

    def label(n: Path) -> str:
        return str(n) if n in outside_nodes else n.name

    lines: list[str] = []
    title = "repo root" if directory == Path(".") else str(directory)
    lines.append(f"# import graph: {title}")
    lines.append("")

    links: list[Link] = []  # (text, target_index_path) for nodes that have a view

    if directory != Path("."):
        parent = directory.parent
        parent_index = view_index_path(out_dir, parent)
        rel_link = os.path.relpath(parent_index, start=index_path.parent)
        lines.append(f"[parent view]({rel_link})")
        lines.append("")

    inside_nodes = nodes - outside_nodes
    lines.extend(
        render_summary(
            repo,
            inside_nodes,
            dirs_with_py,
            pair_edges,
            edge_names,
            label,
            file_scores,
            dir_scores,
        )
    )

    ordered_pairs = sorted(
        pair_edges.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))
    )
    mermaid_lines = build_mermaid_lines(nodes, ids, outside_nodes, label, ordered_pairs)

    lines.append("```mermaid")
    lines.extend(mermaid_lines)

    for n in sorted(nodes, key=str):
        if n in dirs_with_py:
            target_index = view_index_path(out_dir, n)
            rel_link = os.path.relpath(target_index, start=index_path.parent)
            links.append((str(n), rel_link))
    lines.append("```")
    lines.append("")

    if links:
        lines.append("Links:")
        lines.append("")
        for text, rel_link in links:
            lines.append(f"- [{text}]({rel_link})")
        lines.append("")

    if ordered_pairs:
        lines.append("Edges:")
        lines.append("")
        for (src, tgt), file_pairs in ordered_pairs:
            unit = "file pair" if len(file_pairs) == 1 else "file pairs"
            lines.append(f"### {label(src)} -> {label(tgt)} ({len(file_pairs)} {unit})")
            lines.append("")
            for f_rel, t_rel in sorted(file_pairs, key=lambda p: (str(p[0]), str(p[1]))):
                names = sorted(edge_names.get((f_rel, t_rel), ()))
                if names:
                    lines.append(f"- {f_rel} -> {t_rel}: {', '.join(names)}")
                else:
                    lines.append(f"- {f_rel} -> {t_rel}")
            lines.append("")

    index_path.write_text("\n".join(lines), encoding="utf-8")

    if write_html:
        write_html_view(out_dir, directory, mermaid_lines, nodes, dirs_with_py)


__version__ = "0.1.0"


class CheckArgs(argparse.Namespace):
    # placeholder default for the static type only; argparse always
    # overwrites it before parse_args() returns
    repo: Path = Path()


class GraphArgs(argparse.Namespace):
    repo: Path = Path()
    out: Path = Path()
    html: bool = False


class MetricsArgs(argparse.Namespace):
    repo: Path = Path()


def build_graph(
    repo: Path,
) -> tuple[
    list[Path],
    set[Path],
    EdgeNames,
    dict[Path, set[str]],
    dict[Path, set[Path]],
    dict[Path, set[Path]],
    dict[Path, FileScore],
    dict[Path, DirScore],
]:
    """Parses `repo` once and builds every structure the subcommands share:
    the file list, the directories holding Python code, the file-level
    import edges (and their imported names), each file's top-level defined
    names, the forward/reverse file adjacency, and the file/directory
    scores. Prints ambiguous-import warnings to stderr along the way."""
    py_files = find_py_files(repo)
    dirs_with_py, module_candidates, suffix_index = build_index(repo, py_files)
    edge_names, warnings, defined_names = collect_edges(
        repo, py_files, module_candidates, suffix_index
    )

    for importer_rel, cands in warnings:
        cand_strs = ", ".join(str(c.relative_to(repo)) for c in cands)
        print(
            f"warning: ambiguous import in {importer_rel}: candidates {cand_strs}",
            file=sys.stderr,
        )

    nodes = {f.relative_to(repo) for f in py_files}
    adj, radj = build_file_graph(nodes, edge_names)
    dependents, dependencies = compute_reach(nodes, adj, radj)
    file_scores = compute_file_scores(nodes, dependents, dependencies)
    dir_scores = compute_dir_scores(dirs_with_py, dependents, dependencies)

    return (
        py_files,
        dirs_with_py,
        edge_names,
        defined_names,
        adj,
        radj,
        file_scores,
        dir_scores,
    )


def print_check_findings(
    cycle_findings: list[CycleFinding],
    distinct_file_cycles: int,
    privacy_violations: list[PrivacyViolation],
    name_violations: list[NameViolation],
) -> bool:
    """Prints findings, returns True if any finding exists."""
    any_finding = (
        bool(cycle_findings) or bool(privacy_violations) or bool(name_violations)
    )

    print(
        f"## Cycles: {len(cycle_findings)} finding(s) across all directory "
        + f"views, from {distinct_file_cycles} distinct file-level cycle(s)"
    )
    if cycle_findings:
        print(
            "(a directory view merges each child directory into one node, "
            + "so a view's cycles need not match the file-level cycles: "
            + "2 directories can import each other with no file-level cycle, "
            + "and several file-level cycles can merge into one)"
        )
    for cf in cycle_findings:
        directory_label = "repo root" if cf.directory == Path(".") else str(cf.directory)
        print(f"- {directory_label}: {len(cf.members)} nodes")
        print(f"  members: {', '.join(str(m) for m in cf.members)}")
        print(f"  example: {' -> '.join(str(n) for n in cf.example)}")
        for a, b in cf.example_files:
            print(f"    {a} -> {b}")
    print()

    total_private = len(privacy_violations) + len(name_violations)
    print(f"## Private-item violations: {total_private} finding(s)")
    for v in privacy_violations:
        print(f"- {v.importer} -> {v.target}: allowed only inside {v.owner}")
    for nv in name_violations:
        scope = "directory" if nv.owner_is_dir else "file"
        print(
            f"- {nv.importer} -> {nv.target}: name {nv.name} private to "
            + f"{scope} {nv.owner}"
        )
    print()

    return any_finding


def run_check(repo: Path) -> int:
    """`underscope check <repo>`: the import-cycle and privacy-convention
    checks, findings printed to stdout, exit 1 on any finding, no views
    written."""
    py_files, dirs_with_py, edge_names, _, _, _, _, _ = build_graph(repo)

    cycle_findings: list[CycleFinding] = []
    for directory in sorted(dirs_with_py, key=str):
        pair_edges = build_view(directory, edge_names)
        inside_nodes = compute_inside_nodes(directory, pair_edges)
        cycle_findings.extend(find_cycle_findings(directory, inside_nodes, pair_edges))

    package_roots = find_package_roots(repo)
    privacy_violations = find_privacy_violations(edge_names, package_roots)
    name_violations = find_name_violations(edge_names)
    distinct_file_cycles = count_file_level_cycles(py_files, repo, edge_names)
    any_finding = print_check_findings(
        cycle_findings, distinct_file_cycles, privacy_violations, name_violations
    )
    return 1 if any_finding else 0


def run_graph(repo: Path, out_dir: Path, write_html: bool) -> int:
    """`underscope graph <repo> --out DIR [--html]`: writes one mermaid
    view per directory under DIR."""
    out_dir.mkdir(parents=True, exist_ok=True)
    _, dirs_with_py, edge_names, _, _, _, file_scores, dir_scores = build_graph(repo)

    written = 0
    for directory in sorted(dirs_with_py, key=str):
        pair_edges = build_view(directory, edge_names)
        write_view(
            out_dir,
            repo,
            directory,
            dirs_with_py,
            pair_edges,
            edge_names,
            write_html,
            file_scores,
            dir_scores,
        )
        written += 1

    print(f"wrote {written} views under {out_dir}", file=sys.stderr)
    return 0


def run_metrics(repo: Path) -> int:
    """`underscope metrics <repo>`: prints a coupling report to stdout,
    always exits 0."""
    (
        py_files,
        dirs_with_py,
        edge_names,
        defined_names,
        adj,
        radj,
        file_scores,
        dir_scores,
    ) = build_graph(repo)

    print(
        "\n".join(
            render_metrics(
                repo,
                py_files,
                dirs_with_py,
                edge_names,
                file_scores,
                dir_scores,
                defined_names,
                adj,
                radj,
            )
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="underscope",
        description="Check and measure a Python repo's imports: no import cycles, "
        + "underscore privacy scoped like Rust's pub(super)/pub(crate), coupling metrics.",
        epilog="Rules, metrics and blind spots: https://github.com/dertilo/underscope",
    )
    parser.add_argument(
        "--version", action="version", version=f"underscope {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser(
        "check",
        help=(
            "run the import-cycle and privacy convention checks, print "
            + "findings to stdout, exit 1 on any finding"
        ),
    )
    check_parser.add_argument("repo", type=Path)

    graph_parser = subparsers.add_parser("graph", help="write mermaid views")
    graph_parser.add_argument("repo", type=Path)
    graph_parser.add_argument("--out", type=Path, required=True)
    graph_parser.add_argument(
        "--html",
        action="store_true",
        help=(
            "also write index.html per view; needs network access in the "
            + "browser to load mermaid from a CDN"
        ),
    )

    metrics_parser = subparsers.add_parser(
        "metrics",
        help=(
            "print a coupling report (reach and score, interface "
            + "width, exposure share, entry files, misplaced files) to "
            + "stdout; always exits 0"
        ),
    )
    metrics_parser.add_argument("repo", type=Path)

    args = parser.parse_args()

    if args.command == "check":
        check_args = CheckArgs()
        check_args.repo = args.repo
        return run_check(check_args.repo.resolve())
    if args.command == "graph":
        graph_args = GraphArgs()
        graph_args.repo = args.repo
        graph_args.out = args.out
        graph_args.html = args.html
        return run_graph(graph_args.repo.resolve(), graph_args.out, graph_args.html)
    metrics_args = MetricsArgs()
    metrics_args.repo = args.repo
    return run_metrics(metrics_args.repo.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
