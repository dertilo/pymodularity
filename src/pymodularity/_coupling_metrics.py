"""The 3 coupling analyses beyond reach/scores (interface width, exposure
share, entry files), and `render_metrics`, the printer for all 5 of
`pymodularity metrics`'s sections, including name groups from
`_name_groups`."""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from pymodularity._entry_files import compute_entry_files
from pymodularity._import_parse.import_graph import EdgeNames
from pymodularity._name_groups import compute_name_groups
from pymodularity._reach_scores import DirScore, FileScore, ancestor_dirs


@dataclass(frozen=True, slots=True)
class _CountedPath:
    """A path with a count against it (dependents or dependencies)."""

    path: Path
    count: int


def _top_n_by_count(counts: Mapping[Path, int], n: int = 10) -> tuple[_CountedPath, ...]:
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:n]
    return tuple(_CountedPath(p, c) for p, c in ranked)


@dataclass(frozen=True, slots=True)
class _FileWidth:
    """A target file's interface width: distinct names other files import
    from it, and distinct files importing any of those names."""

    target: Path
    names: int
    importers: int


def _compute_file_widths(
    nodes: AbstractSet[Path], edge_names: EdgeNames
) -> tuple[_FileWidth, ...]:
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
    return tuple(widths)


def _compute_wrapper_dirs(
    nodes: AbstractSet[Path], dirs_with_py: AbstractSet[Path]
) -> frozenset[Path]:
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

    return frozenset(
        d
        for d in dirs_with_py
        if direct_file_count.get(d, 0) == 0 and len(children.get(d, [])) == 1
    )


@dataclass(frozen=True, slots=True)
class _DirWidth:
    """A directory's interface width: distinct (target file, name) pairs
    files outside it import from files inside it, and distinct outside
    importing files."""

    directory: Path
    names: int
    importers: int


def _compute_dir_widths(
    dirs_with_py: AbstractSet[Path], edge_names: EdgeNames
) -> tuple[_DirWidth, ...]:
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

    widths: list[_DirWidth] = []
    for directory in dirs_with_py:
        name_count = len(named_pairs.get(directory, set())) + len(
            bare_targets.get(directory, set())
        )
        importer_count = len(importers.get(directory, set()))
        widths.append(_DirWidth(directory, name_count, importer_count))
    return tuple(widths)


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
    nodes: AbstractSet[Path],
    edge_names: EdgeNames,
    defined_names: Mapping[Path, AbstractSet[str]],
) -> Mapping[Path, _Exposure]:
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
        defined = defined_names.get(f, frozenset())
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


def render_metrics(
    repo: Path,
    py_files: Sequence[Path],
    dirs_with_py: AbstractSet[Path],
    edge_names: EdgeNames,
    attr_name_edges: EdgeNames,
    file_scores: Mapping[Path, FileScore],
    dir_scores: Mapping[Path, DirScore],
    defined_names: Mapping[Path, AbstractSet[str]],
    adj: Mapping[Path, AbstractSet[Path]],
) -> tuple[str, ...]:
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
        + "to; reach-score(f) = dependents(f) x dependencies(f)."
    )
    lines.append("")
    lines.append(f"- N (non-test .py files): {n}")
    lines.append(f"- file-level edges: {len(edge_names)}")
    lines.append("")

    lines.append("Top 10 by dependents:")
    lines.append("")
    for cp in _top_n_by_count(dependents):
        share = cp.count / n * 100 if n else 0.0
        lines.append(f"- {cp.path}: {cp.count} ({share:.1f}% of N)")
    lines.append("")

    lines.append("Top 10 by dependencies:")
    lines.append("")
    for cp in _top_n_by_count(dependencies):
        share = cp.count / n * 100 if n else 0.0
        lines.append(f"- {cp.path}: {cp.count} ({share:.1f}% of N)")
    lines.append("")

    lines.append(
        "All files by reach-score (dependents x dependencies), ties: dependents "
        + "desc, then path:"
    )
    lines.append("")
    lines.append("| file | dependents | dependencies | reach-score |")
    lines.append("| --- | --- | --- | --- |")
    for fs in sorted(
        file_scores.values(),
        key=lambda s: (-s.reach_score, -s.dependents, str(s.path)),
    ):
        lines.append(
            f"| {fs.path} | {fs.dependents} | {fs.dependencies} | {fs.reach_score} |"
        )
    lines.append("")

    lines.append(
        "All directories by reach-score (in x out, measured across the "
        + "directory's own boundary, repo-wide); a directory whose file "
        + "set is identical to its one child directory's is skipped, "
        + "keeping only the deepest directory of such a wrapper chain:"
    )
    lines.append("")
    lines.append("| directory | in | out | reach-score |")
    lines.append("| --- | --- | --- | --- |")
    for ds in sorted(
        (s for s in dir_scores.values() if s.path not in wrapper_dirs),
        key=lambda s: (-s.reach_score, str(s.path)),
    ):
        directory_label = "repo root" if ds.path == Path(".") else str(ds.path)
        lines.append(
            f"| {directory_label} | {ds.reach_in} | {ds.reach_out} | {ds.reach_score} |"
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

    lines.append("## Name groups")
    lines.append("")
    lines.append(
        "Per file (`__init__.py` excluded): its top-level names, joined "
        + "when one importer imports them together or one name's body "
        + "references the other, with a re-export fix -- an import "
        + "reached through a package `__init__.py` counts against the "
        + "file that defines the name, not the `__init__.py` itself. A "
        + "group counts only if it has >= 1 importer and >= 1 "
        + "def/async def/class name. A file is listed when it has >= 2 "
        + "counting groups. See docs/name_groups.md for the join rules and "
        + "how to read a finding."
    )
    lines.append("")
    for fg in compute_name_groups(
        repo, py_files, defined_names, edge_names, attr_name_edges
    ):
        lines.append(f"- {fg.path}")
        for g in fg.groups:
            lines.append(f"  - names: {', '.join(g.names)}")
            lines.append(f"    importers: {', '.join(str(p) for p in g.importers)}")
    lines.append("")

    return tuple(lines)
