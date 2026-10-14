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
for the _SCC_FULL_EDGE_LIMIT threshold controlling full vs. collapsed
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
import sys
from pathlib import Path

from underscope._check import (
    find_name_violations,
    find_package_roots,
    find_privacy_violations,
    print_check_findings,
)
from underscope._cycles import count_file_level_cycles, find_all_cycle_findings
from underscope._metrics import compute_scores, render_metrics
from underscope._resolve import build_graph
from underscope._view_edges import build_view
from underscope._views import write_view

__version__ = "0.1.0"


class _CheckArgs(argparse.Namespace):
    # placeholder default for the static type only; argparse always
    # overwrites it before parse_args() returns
    repo: Path = Path()


class _GraphArgs(argparse.Namespace):
    repo: Path = Path()
    out: Path = Path()
    html: bool = False


class _MetricsArgs(argparse.Namespace):
    repo: Path = Path()


def _run_check(repo: Path) -> int:
    """`underscope check <repo>`: the import-cycle and privacy-convention
    checks, findings printed to stdout, exit 1 on any finding, no views
    written."""
    graph = build_graph(repo)

    cycle_findings = find_all_cycle_findings(graph.dirs_with_py, graph.edge_names)

    package_roots = find_package_roots(repo)
    privacy_violations = find_privacy_violations(graph.edge_names, package_roots)
    name_violations = find_name_violations(graph.edge_names)
    distinct_file_cycles = count_file_level_cycles(graph.adj)
    any_finding = print_check_findings(
        cycle_findings, distinct_file_cycles, privacy_violations, name_violations
    )
    return 1 if any_finding else 0


def _run_graph(repo: Path, out_dir: Path, write_html: bool) -> int:
    """`underscope graph <repo> --out DIR [--html]`: writes one mermaid
    view per directory under DIR."""
    out_dir.mkdir(parents=True, exist_ok=True)
    graph = build_graph(repo)
    file_scores, dir_scores = compute_scores(graph.dirs_with_py, graph.adj, graph.radj)

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
            file_scores,
            dir_scores,
        )
        written += 1

    print(f"wrote {written} views under {out_dir}", file=sys.stderr)
    return 0


def _run_metrics(repo: Path) -> int:
    """`underscope metrics <repo>`: prints a coupling report to stdout,
    always exits 0."""
    graph = build_graph(repo)
    file_scores, dir_scores = compute_scores(graph.dirs_with_py, graph.adj, graph.radj)

    print(
        "\n".join(
            render_metrics(
                graph.dirs_with_py,
                graph.edge_names,
                file_scores,
                dir_scores,
                graph.defined_names,
                graph.adj,
                graph.radj,
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
        check_args = _CheckArgs()
        check_args.repo = args.repo
        return _run_check(check_args.repo.resolve())
    if args.command == "graph":
        graph_args = _GraphArgs()
        graph_args.repo = args.repo
        graph_args.out = args.out
        graph_args.html = args.html
        return _run_graph(graph_args.repo.resolve(), graph_args.out, graph_args.html)
    metrics_args = _MetricsArgs()
    metrics_args.repo = args.repo
    return _run_metrics(metrics_args.repo.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
