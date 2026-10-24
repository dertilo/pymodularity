"""`pymodularity graph <repo> --out DIR [--html]`: writes one mermaid view
per directory under DIR. `--html` also writes an index.html per view
(mermaid diagram, clickable directory nodes) alongside index.md; the
browser needs network access to load mermaid from a CDN. Nodes are file
paths (or directory paths for namespace packages without __init__.py);
edge labels count distinct (importer file, target file) pairs. Each view
opens with a summary: cycles are strongly connected components (size >= 2)
among the view's inside children on the view's own edges, outside (dashed)
nodes excluded; the node table lists one row per inside child. Both
sections carry their own legend text in the rendered output defining each
count; see that legend for units and for the _SCC_FULL_EDGE_LIMIT
threshold controlling full vs. collapsed one-way-edge lists in Cycles.

`--exclude PATTERN` (fnmatch against the POSIX path relative to `<repo>`,
matched on the path itself or any parent directory) is repeatable, merged
with `[tool.pymodularity] exclude` in `<repo>`'s own pyproject.toml, never
replacing it. An excluded tree is invisible, no view written for it."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pymodularity._graph_views.mermaid_render import write_view
from pymodularity._import_parse.import_graph import build_graph
from pymodularity._reach_scores import compute_scores
from pymodularity._view_edges import build_view


class GraphArgs(argparse.Namespace):
    repo: Path = Path()
    out: Path = Path()
    html: bool = False
    exclude: tuple[str, ...] = ()


def add_graph_parser(subparsers: argparse._SubParsersAction, exclude_help: str) -> None:
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
    graph_parser.add_argument("--exclude", action="append", help=exclude_help)


def run_graph(
    repo: Path, out_dir: Path, write_html: bool, excludes: Sequence[str] = ()
) -> int:
    """`pymodularity graph <repo> --out DIR [--html]`: writes one mermaid
    view per directory under DIR. `excludes` makes a matching tree
    invisible, no view written for it."""
    out_dir.mkdir(parents=True, exist_ok=True)
    graph = build_graph(repo, excludes)
    scores = compute_scores(graph.dirs_with_py, graph.adj, graph.radj)

    written = 0
    for directory in sorted(graph.dirs_with_py, key=str):
        pair_edges = build_view(directory, graph.edge_names)
        write_view(
            out_dir,
            repo,
            directory,
            graph.dirs_with_py,
            pair_edges,
            graph.edge_names,
            write_html,
            scores.file_scores,
            scores.dir_scores,
        )
        written += 1

    print(f"wrote {written} views under {out_dir}", file=sys.stderr)
    return 0
