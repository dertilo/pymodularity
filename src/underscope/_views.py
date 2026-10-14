"""Mermaid and HTML views: one per directory, with a markdown link list to
zoom in/out, plus the node summary table (`_summary`)."""

import html
import os
from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from pathlib import Path

from underscope._graph import EdgeKey, EdgeNames
from underscope._scores import DirScore, FileScore
from underscope._summary import render_summary
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
