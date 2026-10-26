"""`pymodularity metrics <repo>` prints a coupling report to stdout on the
same file graph (reach and score per file and per directory, interface
width per file and per directory, exposure share, entry files, importer
distance) and always exits 0: it is a report, never a gate.

`--exclude PATTERN` (fnmatch against the POSIX path relative to `<repo>`,
matched on the path itself or any parent directory) is repeatable, merged
with `[tool.pymodularity] exclude` in `<repo>`'s own pyproject.toml, never
replacing it. An excluded tree is invisible to the report."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from pymodularity._coupling_metrics import render_metrics
from pymodularity._import_parse.import_graph import build_graph
from pymodularity._import_parse.package_roots import find_package_roots
from pymodularity._reach_scores import compute_scores


class MetricsArgs(argparse.Namespace):
    repo: Path = Path()
    exclude: tuple[str, ...] = ()


def add_metrics_parser(subparsers: argparse._SubParsersAction, exclude_help: str) -> None:
    metrics_parser = subparsers.add_parser(
        "metrics",
        help=(
            "print a coupling report (reach and score, interface "
            + "width, exposure share, entry files, importer distance) to "
            + "stdout; always exits 0"
        ),
    )
    metrics_parser.add_argument("repo", type=Path)
    metrics_parser.add_argument("--exclude", action="append", help=exclude_help)


def run_metrics(repo: Path, excludes: Sequence[str] = ()) -> int:
    """`pymodularity metrics <repo>`: prints a coupling report to stdout,
    always exits 0. `excludes` makes a matching tree invisible to the
    report."""
    graph = build_graph(repo, excludes)
    scores = compute_scores(graph.dirs_with_py, graph.adj, graph.radj)
    package_roots = find_package_roots(repo, excludes)

    print(
        "\n".join(
            render_metrics(
                graph.dirs_with_py,
                graph.edge_names,
                scores.file_scores,
                scores.dir_scores,
                graph.defined_names,
                graph.adj,
                graph.radj,
                package_roots,
            )
        )
    )
    return 0
