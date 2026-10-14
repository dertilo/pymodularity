"""Reach, file/directory scores, and the 5 coupling reports `underscope
metrics` prints."""

from dataclasses import dataclass
from pathlib import Path

from underscope._resolve import EdgeNames


def _reachable_set(start: Path, adj: dict[Path, set[Path]]) -> set[Path]:
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


def _compute_reach(
    nodes: set[Path], adj: dict[Path, set[Path]], radj: dict[Path, set[Path]]
) -> tuple[dict[Path, set[Path]], dict[Path, set[Path]]]:
    """Per file: the set of dependents (other files with an import path to
    it) and the set of dependencies (other files it has an import path
    to), both transitive."""
    dependents = {n: _reachable_set(n, radj) for n in nodes}
    dependencies = {n: _reachable_set(n, adj) for n in nodes}
    return dependents, dependencies


def _top_n_by_count(counts: dict[Path, int], n: int = 10) -> list[tuple[Path, int]]:
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


def _compute_file_scores(
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


def compute_scores(
    dirs_with_py: set[Path],
    adj: dict[Path, set[Path]],
    radj: dict[Path, set[Path]],
) -> tuple[dict[Path, FileScore], dict[Path, DirScore]]:
    """`graph` and `metrics`'s shared first step: reach, then file and
    directory scores from it."""
    nodes = set(adj)
    dependents, dependencies = _compute_reach(nodes, adj, radj)
    file_scores = _compute_file_scores(nodes, dependents, dependencies)
    dir_scores = _compute_dir_scores(dirs_with_py, dependents, dependencies)
    return file_scores, dir_scores


def _ancestor_dirs(rel: Path) -> list[Path]:
    """The directory holding `rel` and every ancestor directory up to the
    repo root ('.'), inclusive."""
    out = [rel.parent]
    while out[-1] != Path("."):
        out.append(out[-1].parent)
    return out


def _compute_dir_scores(
    dirs_with_py: set[Path],
    dependents: dict[Path, set[Path]],
    dependencies: dict[Path, set[Path]],
) -> dict[Path, DirScore]:
    dir_in: dict[Path, set[Path]] = {}
    dir_out: dict[Path, set[Path]] = {}
    for f, dep_set in dependents.items():
        for d in _ancestor_dirs(f):
            if d in dirs_with_py:
                dir_in.setdefault(d, set()).update(dep_set)
    for f, dep_set in dependencies.items():
        for d in _ancestor_dirs(f):
            if d in dirs_with_py:
                dir_out.setdefault(d, set()).update(dep_set)

    scores: dict[Path, DirScore] = {}
    for d in dirs_with_py:
        in_count = sum(1 for g in dir_in.get(d, set()) if not g.is_relative_to(d))
        out_count = sum(1 for g in dir_out.get(d, set()) if not g.is_relative_to(d))
        scores[d] = DirScore(d, in_count, out_count, in_count * out_count)
    return scores


@dataclass(frozen=True, slots=True)
class _FileWidth:
    """A target file's interface width: distinct names other files import
    from it, and distinct files importing any of those names."""

    target: Path
    names: int
    importers: int


def _compute_file_widths(nodes: set[Path], edge_names: EdgeNames) -> list[_FileWidth]:
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

    widths: list[_FileWidth] = []
    for target in nodes:
        name_count = len(names_by_target.get(target, set())) + (
            1 if target in bare_targets else 0
        )
        importer_count = len(importers_by_target.get(target, set()))
        widths.append(_FileWidth(target, name_count, importer_count))
    return widths


def _compute_wrapper_dirs(nodes: set[Path], dirs_with_py: set[Path]) -> set[Path]:
    """Directories that hold no .py file of their own and have exactly one
    child directory with Python code, so their file set is identical to
    that child's (and, up a wrapper chain, to the file set of every other
    wrapper in the chain). `metrics`'s directory tables skip these,
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
class _DirWidth:
    """A directory's interface width: distinct (target file, name) pairs
    files outside it import from files inside it, and distinct outside
    importing files."""

    directory: Path
    names: int
    importers: int


def _compute_dir_widths(
    dirs_with_py: set[Path], edge_names: EdgeNames
) -> list[_DirWidth]:
    named_pairs: dict[Path, set[tuple[Path, str]]] = {}
    bare_targets: dict[Path, set[Path]] = {}
    importers: dict[Path, set[Path]] = {}
    for (importer_rel, target_rel), names in edge_names.items():
        for directory in _ancestor_dirs(target_rel):
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

    widths: list[_DirWidth] = []
    for directory in dirs_with_py:
        name_count = len(named_pairs.get(directory, set())) + len(
            bare_targets.get(directory, set())
        )
        importer_count = len(importers.get(directory, set()))
        widths.append(_DirWidth(directory, name_count, importer_count))
    return widths


@dataclass(frozen=True, slots=True)
class _Exposure:
    """A file's exposure share: how many of its top-level defined names
    (def, async def, class, plain/annotated assignment to a bare name;
    imports don't count) at least one other file actually imports."""

    path: Path
    used: int
    defined: int
    share: float
    importers: int


def _compute_exposure(
    nodes: set[Path], edge_names: EdgeNames, defined_names: dict[Path, set[str]]
) -> dict[Path, _Exposure]:
    imported_names: dict[Path, set[str]] = {}
    importers_by_name: dict[Path, dict[str, set[Path]]] = {}
    for (importer_rel, target_rel), names in edge_names.items():
        if not names or importer_rel == target_rel:
            continue
        imported_names.setdefault(target_rel, set()).update(names)
        per_name = importers_by_name.setdefault(target_rel, {})
        for name in names:
            per_name.setdefault(name, set()).add(importer_rel)

    exposures: dict[Path, _Exposure] = {}
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
        exposures[f] = _Exposure(f, used, total, share, len(importer_set))
    return exposures


@dataclass(frozen=True, slots=True)
class _EntryFiles:
    """A directory's entry-file share: how many of its files at least one
    file outside the directory imports directly, out of all files inside
    it; outside_importers is the distinct outside files doing so."""

    directory: Path
    entry: int
    files: int
    share: float
    outside_importers: int


def _compute_entry_files(
    dirs_with_py: set[Path], nodes: set[Path], edge_names: EdgeNames
) -> dict[Path, _EntryFiles]:
    entry_by_dir: dict[Path, set[Path]] = {}
    outside_importers_by_dir: dict[Path, set[Path]] = {}
    for importer_rel, target_rel in edge_names:
        for directory in _ancestor_dirs(target_rel):
            if directory not in dirs_with_py:
                continue
            if importer_rel.is_relative_to(directory):
                continue
            entry_by_dir.setdefault(directory, set()).add(target_rel)
            outside_importers_by_dir.setdefault(directory, set()).add(importer_rel)

    files_by_dir: dict[Path, int] = {}
    for f in nodes:
        for directory in _ancestor_dirs(f):
            if directory in dirs_with_py:
                files_by_dir[directory] = files_by_dir.get(directory, 0) + 1

    out: dict[Path, _EntryFiles] = {}
    for directory in dirs_with_py:
        files = files_by_dir.get(directory, 0)
        entry = len(entry_by_dir.get(directory, set()))
        share = entry / files if files else 0.0
        outside = len(outside_importers_by_dir.get(directory, set()))
        out[directory] = _EntryFiles(directory, entry, files, share, outside)
    return out


@dataclass(frozen=True, slots=True)
class _Misplaced:
    """A file whose direct import partners (files it imports plus files
    importing it, from the same file graph the scores use) cluster more
    heavily in some other directory than in its own."""

    path: Path
    own: int
    other: int
    other_dir: Path
    imported_by: int
    imports: int


def _compute_misplaced(
    nodes: set[Path],
    adj: dict[Path, set[Path]],
    radj: dict[Path, set[Path]],
) -> list[_Misplaced]:
    results: list[_Misplaced] = []
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
            results.append(_Misplaced(f, own, other, other_dir, imported_by, imports))
    return results


def render_metrics(
    dirs_with_py: set[Path],
    edge_names: EdgeNames,
    file_scores: dict[Path, FileScore],
    dir_scores: dict[Path, DirScore],
    defined_names: dict[Path, set[str]],
    adj: dict[Path, set[Path]],
    radj: dict[Path, set[Path]],
) -> list[str]:
    nodes = set(adj)
    n = len(nodes)
    dependents = {p: fs.dependents for p, fs in file_scores.items()}
    dependencies = {p: fs.dependencies for p, fs in file_scores.items()}
    wrapper_dirs = _compute_wrapper_dirs(nodes, dirs_with_py)

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
    for path, count in _top_n_by_count(dependents):
        share = count / n * 100 if n else 0.0
        lines.append(f"- {path}: {count} ({share:.1f}% of N)")
    lines.append("")

    lines.append("Top 10 by dependencies:")
    lines.append("")
    for path, count in _top_n_by_count(dependencies):
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
        _compute_file_widths(nodes, edge_names),
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
            for w in _compute_dir_widths(dirs_with_py, edge_names)
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
        for e in _compute_exposure(nodes, edge_names, defined_names).values()
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
        for e in _compute_entry_files(dirs_with_py, nodes, edge_names).values()
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
        _compute_misplaced(nodes, adj, radj),
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
