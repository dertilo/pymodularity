"""File discovery, import resolution and file-level edge collection: build
the whole-repo Python import graph with ast (stdlib only). Shared types
(PathParts, EdgeNames, PairEdges, ...) used by every other module live
here too."""

import ast
import os
import sys
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


def build_file_graph(
    nodes: set[Path], edge_names: EdgeNames
) -> tuple[dict[Path, set[Path]], dict[Path, set[Path]]]:
    """The raw file-level import graph (forward adjacency and its reverse),
    same edges `check`'s cycle rule uses: no self-edges (collect_edges
    already drops those), duplicates already collapsed by edge_names'
    dict keys."""
    adj: dict[Path, set[Path]] = {n: set() for n in nodes}
    radj: dict[Path, set[Path]] = {n: set() for n in nodes}
    for importer_rel, target_rel in edge_names:
        if importer_rel in adj and target_rel in adj and importer_rel != target_rel:
            adj[importer_rel].add(target_rel)
            radj[target_rel].add(importer_rel)
    return adj, radj


def build_graph(
    repo: Path,
) -> tuple[
    list[Path],
    set[Path],
    EdgeNames,
    dict[Path, set[str]],
    dict[Path, set[Path]],
    dict[Path, set[Path]],
]:
    """Parses `repo` once and builds every structure every subcommand needs:
    the file list, the directories holding Python code, the file-level
    import edges (and their imported names), each file's top-level defined
    names, and the forward/reverse file adjacency. Prints ambiguous-import
    warnings to stderr along the way. Reach and file/directory scores are
    not computed here -- `check` never uses them, so `graph` and `metrics`
    compute them in their own run_* path."""
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

    return py_files, dirs_with_py, edge_names, defined_names, adj, radj
