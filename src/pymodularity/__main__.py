"""pymodularity: see and check a repo's real Python imports.

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
`a.b` exactly, never shorter.

WARNING, blind spot: the graph shows import coupling only, a lower bound on
logical coupling ("if A changes, must B?"). Coupling that bypasses Python
imports stays invisible: HTTP APIs, shared database schemas and tables,
config keys and env vars, message and file formats, string-keyed registries,
runtime sys.path edits. Two services with no edge here can still force each
other's edits. Never read a missing edge as independence.

Three subcommands, each documented in its own module: `graph_command`,
`check_command`, `metrics_command`. Every subcommand also takes a
repeatable `--exclude PATTERN` (fnmatch against the POSIX path relative to
`<repo>`, matched on the path itself or any parent directory), merged with
`[tool.pymodularity] exclude` in `<repo>`'s own pyproject.toml, never
replacing it."""

import argparse
from collections.abc import Sequence
from pathlib import Path

from pymodularity import __version__
from pymodularity._import_parse.package_roots import load_exclude_config
from pymodularity.check_command import CheckArgs, add_check_parser, run_check
from pymodularity.graph_command import GraphArgs, add_graph_parser, run_graph
from pymodularity.metrics_command import (
    MetricsArgs,
    add_metrics_parser,
    run_metrics,
)

_EXCLUDE_HELP = (
    "fnmatch pattern (repeatable), against the POSIX path relative to "
    "repo, matched against a file or any of its parent directories; adds "
    "to, never replaces, [tool.pymodularity] exclude in repo's own "
    "pyproject.toml"
)


def _merged_excludes(repo: Path, cli_excludes: Sequence[str]) -> tuple[str, ...]:
    """Config (`[tool.pymodularity] exclude` in the repo's own
    pyproject.toml) merged with `--exclude` from the command line, which
    adds to the config list rather than replacing it. Order preserved,
    duplicates dropped."""
    merged = list(load_exclude_config(repo)) + list(cli_excludes)
    return tuple(dict.fromkeys(merged))


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="pymodularity",
        description="Check and measure a Python repo's imports: no import cycles, "
        + "underscore privacy scoped like Rust's pub(super)/pub(crate), coupling metrics.",
        epilog="Rules, metrics and blind spots: https://github.com/dertilo/pymodularity",
    )
    parser.add_argument(
        "--version", action="version", version=f"pymodularity {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_check_parser(subparsers, _EXCLUDE_HELP)
    add_graph_parser(subparsers, _EXCLUDE_HELP)
    add_metrics_parser(subparsers, _EXCLUDE_HELP)

    args = parser.parse_args()

    if args.command == "check":
        check_args = CheckArgs()
        check_args.repo = args.repo.resolve()
        check_args.deps = args.deps
        check_args.exclude = _merged_excludes(check_args.repo, args.exclude or [])
        return run_check(check_args.repo, check_args.deps, check_args.exclude)
    if args.command == "graph":
        graph_args = GraphArgs()
        graph_args.repo = args.repo.resolve()
        graph_args.out = args.out
        graph_args.html = args.html
        graph_args.exclude = _merged_excludes(graph_args.repo, args.exclude or [])
        return run_graph(
            graph_args.repo, graph_args.out, graph_args.html, graph_args.exclude
        )
    metrics_args = MetricsArgs()
    metrics_args.repo = args.repo.resolve()
    metrics_args.exclude = _merged_excludes(metrics_args.repo, args.exclude or [])
    return run_metrics(metrics_args.repo, metrics_args.exclude)


if __name__ == "__main__":
    # nosemgrep: no-raise-outside-boundary  # boundary: CLI entry point, the process exit boundary
    raise SystemExit(main())
