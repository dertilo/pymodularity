"""A directory view's node summary table: one row per inside child, with
its score columns (via `_scores`) and the Cycles section (via `_cycles`),
rendered as the markdown block `render.write_view` embeds."""

from collections.abc import Callable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from underscope._cycles import compute_sccs
from underscope._parse.graph import EdgeKey, EdgeNames
from underscope._parse.names import is_private_name
from underscope._scores import DirScore, FileScore
from underscope._view_edges import PairEdges


@dataclass(frozen=True, slots=True)
class _NamedCount:
    """One imported name with the count of units importing it."""

    name: str
    count: int


@dataclass(frozen=True, slots=True)
class _InitSplit:
    """A node with an `__init__.py`: how its incoming edges split between
    the init file itself and the other files inside it."""

    root: int
    internals: int


@dataclass(frozen=True, slots=True)
class _NoInitFile:
    """A node with no `__init__.py` to split incoming edges against."""


@dataclass(frozen=True, slots=True)
class _NodeRow:
    """One inside child's row in a view's summary node table."""

    node: Path
    importing_units: int
    distinct_names: int
    top_names: tuple[_NamedCount, ...]
    internal: int
    outgoing: int
    init_split: _InitSplit | _NoInitFile
    private: int
    reach_a: int
    reach_b: int
    score: int


def _compute_node_rows(
    repo: Path,
    inside_nodes: AbstractSet[Path],
    dirs_with_py: AbstractSet[Path],
    pair_edges: PairEdges,
    edge_names: EdgeNames,
    file_scores: Mapping[Path, FileScore],
    dir_scores: Mapping[Path, DirScore],
) -> tuple[_NodeRow, ...]:
    rows: list[_NodeRow] = []
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
                    if is_private_name(name):
                        private += 1

        top_names = sorted(name_units.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:5]
        top_names_fmt = tuple(_NamedCount(name, len(units)) for name, units in top_names)

        internal_pairs = {
            (f_rel, t_rel)
            for (f_rel, t_rel) in edge_names
            if f_rel.is_relative_to(node) and t_rel.is_relative_to(node)
        }

        outgoing_pairs: set[EdgeKey] = set()
        for (src, tgt), file_pairs in pair_edges.items():
            if src == node and tgt != node:
                outgoing_pairs |= file_pairs

        init_split: _InitSplit | _NoInitFile
        init_path = node / "__init__.py"
        if node in dirs_with_py and (repo / init_path).is_file():
            root = sum(1 for _, t_rel in incoming_file_pairs if t_rel == init_path)
            init_split = _InitSplit(root, len(incoming_file_pairs) - root)
        else:
            init_split = _NoInitFile()

        if node in dirs_with_py:
            ds = dir_scores[node]
            reach_a, reach_b, score = ds.reach_in, ds.reach_out, ds.score
        else:
            fs = file_scores[node]
            reach_a, reach_b, score = fs.dependents, fs.dependencies, fs.score

        rows.append(
            _NodeRow(
                node,
                len(importing_units),
                len(distinct_target_names),
                top_names_fmt,
                len(internal_pairs),
                len(outgoing_pairs),
                init_split,
                private,
                reach_a,
                reach_b,
                score,
            )
        )

    rows.sort(key=lambda r: (-r.importing_units, str(r.node)))
    return tuple(rows)


# an SCC with at most this many internal edges (one per ordered (src, tgt)
# child pair, not file pairs) lists every edge; above it, only mutual pairs
# are listed plus a count of the rest (see Edges for those).
_SCC_FULL_EDGE_LIMIT = 30


def render_summary(
    repo: Path,
    inside_nodes: AbstractSet[Path],
    dirs_with_py: AbstractSet[Path],
    pair_edges: PairEdges,
    edge_names: EdgeNames,
    label: Callable[[Path], str],
    file_scores: Mapping[Path, FileScore],
    dir_scores: Mapping[Path, DirScore],
) -> tuple[str, ...]:
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
            if not scc.mutual_pairs or len(scc.edges) <= _SCC_FULL_EDGE_LIMIT:
                # A->B->C->A with no mutual pair: the one-way edges are the
                # cycle itself, so keep the full list instead of collapsing
                # it. Small SCCs (<= _SCC_FULL_EDGE_LIMIT internal edges) also
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
            f"An SCC with at most {_SCC_FULL_EDGE_LIMIT} internal edges (one "
            + "per ordered child pair) lists every one-way edge as "
            + "`a -> b (N)`; above that, the one-way edges shrink to a count, "
            + "see Edges, unless the SCC has no mutual pair."
        )
    lines.append("")

    lines.append("## Node summary")
    lines.append("")
    rows = _compute_node_rows(
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
            top_str = ", ".join(f"{nc.name} ({nc.count})" for nc in row.top_names)
            names_cell = f"{row.distinct_names} ({top_str})"
        root_cell = (
            "-"
            if isinstance(row.init_split, _NoInitFile)
            else f"{row.init_split.root}/{row.init_split.internals}"
        )
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
    return tuple(lines)
