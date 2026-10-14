"""Mermaid and HTML views: one per directory, with a markdown link list to
zoom in/out, plus the node summary table (score columns via `_metrics`,
cycle section via `_cycles`)."""

import html
import os
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from underscope._cycles import compute_sccs
from underscope._metrics import DirScore, FileScore
from underscope._resolve import EdgeKey, EdgeNames, is_private_name
from underscope._view_edges import PairEdges, PairKey, compute_inside_nodes

# (link text, relative link target) for the markdown "Links:" list
_Link = tuple[str, str]


def _mermaid_id(path: Path) -> str:
    s = str(path).replace("\\", "/")
    return "n_" + "".join(c if c.isalnum() else "_" for c in s)


def _view_index_path(out_dir: Path, directory: Path, name: str = "index.md") -> Path:
    if directory == Path("."):
        return out_dir / name
    return out_dir / directory / name


@dataclass(frozen=True, slots=True)
class _NamedCount:
    """One imported name with the count of units importing it."""

    name: str
    count: int


@dataclass(frozen=True, slots=True)
class _NodeRow:
    """One inside child's row in a view's summary node table."""

    node: Path
    importing_units: int
    distinct_names: int
    top_names: tuple[_NamedCount, ...]
    internal: int
    outgoing: int
    root: int | None
    internals: int | None
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
            _NodeRow(
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
    return tuple(rows)


# an SCC with at most this many internal edges (one per ordered (src, tgt)
# child pair, not file pairs) lists every edge; above it, only mutual pairs
# are listed plus a count of the rest (see Edges for those).
_SCC_FULL_EDGE_LIMIT = 30


def _render_summary(
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
    return tuple(lines)


_MERMAID_CDN_URL = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs"
# headroom above mermaid's defaults (maxTextSize 50000, maxEdges 500): the
# largest view seen in practice had a ~38000-char mermaid block with ~400
# edges, so double both defaults.
_MERMAID_MAX_TEXT_SIZE = 100_000
_MERMAID_MAX_EDGES = 1_000


def _build_mermaid_lines(
    nodes: AbstractSet[Path],
    ids: Mapping[Path, str],
    outside_nodes: AbstractSet[Path],
    label: Callable[[Path], str],
    ordered_pairs: Sequence[tuple[PairKey, AbstractSet[EdgeKey]]],
) -> tuple[str, ...]:
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
    return tuple(lines)


def _write_html_view(
    out_dir: Path,
    directory: Path,
    mermaid_lines: Sequence[str],
    nodes: AbstractSet[Path],
    dirs_with_py: AbstractSet[Path],
) -> None:
    html_path = _view_index_path(out_dir, directory, "index.html")
    title = "repo root" if directory == Path(".") else str(directory)

    body_lines = list(mermaid_lines)
    for n in sorted(nodes, key=str):
        if n in dirs_with_py:
            target_html = _view_index_path(out_dir, n, "index.html")
            rel_link = os.path.relpath(target_html, start=html_path.parent)
            body_lines.append(f'    click {_mermaid_id(n)} "{rel_link}"')
    mermaid_src = html.escape("\n".join(body_lines))

    parent_link_html = ""
    if directory != Path("."):
        parent_index = _view_index_path(out_dir, directory.parent, "index.html")
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
import mermaid from "{_MERMAID_CDN_URL}";
mermaid.initialize({{
  startOnLoad: true,
  securityLevel: "loose",
  maxTextSize: {_MERMAID_MAX_TEXT_SIZE},
  maxEdges: {_MERMAID_MAX_EDGES},
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
    dirs_with_py: AbstractSet[Path],
    pair_edges: PairEdges,
    edge_names: EdgeNames,
    write_html: bool,
    file_scores: Mapping[Path, FileScore],
    dir_scores: Mapping[Path, DirScore],
) -> None:
    index_path = _view_index_path(out_dir, directory)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    nodes: set[Path] = set()
    for src, tgt in pair_edges:
        nodes.add(src)
        nodes.add(tgt)

    ids = {n: _mermaid_id(n) for n in nodes}
    inside_nodes = compute_inside_nodes(directory, pair_edges)
    outside_nodes = nodes - inside_nodes

    def label(n: Path) -> str:
        return str(n) if n in outside_nodes else n.name

    lines: list[str] = []
    title = "repo root" if directory == Path(".") else str(directory)
    lines.append(f"# import graph: {title}")
    lines.append("")

    links: list[_Link] = []  # (text, target_index_path) for nodes that have a view

    if directory != Path("."):
        parent = directory.parent
        parent_index = _view_index_path(out_dir, parent)
        rel_link = os.path.relpath(parent_index, start=index_path.parent)
        lines.append(f"[parent view]({rel_link})")
        lines.append("")

    lines.extend(
        _render_summary(
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
    mermaid_lines = _build_mermaid_lines(nodes, ids, outside_nodes, label, ordered_pairs)

    lines.append("```mermaid")
    lines.extend(mermaid_lines)

    for n in sorted(nodes, key=str):
        if n in dirs_with_py:
            target_index = _view_index_path(out_dir, n)
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
        _write_html_view(out_dir, directory, mermaid_lines, nodes, dirs_with_py)
