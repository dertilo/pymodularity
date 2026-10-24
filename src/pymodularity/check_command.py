"""`pymodularity check <repo>`: runs six convention checks and prints
findings to stdout, exit 1 on any finding: (1) no import cycles, at every
directory level (any SCC with more than 1 node in any directory's view,
computed the same way the view's own Cycles section is, TYPE_CHECKING and
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
to that directory and only files under it may import it. The same scope
rule also covers the attribute form, `import a.b.mod; a.b.mod._x` or
`import a; a._internal.x` -- a private module or name reached by
attribute chain from a name an import bound, not just one named in an
import statement itself. Findings from this form print the same as the
import form, with a ` (via attribute access)` suffix. Blind spots: an
instance attribute (`obj._y`) needs type information this tool doesn't
have -- ruff's SLF001 is the complement there, though unlike this rule it
has no owner/descendant scope, so it also flags access from inside the
owning file or directory; a base name bound to a third-party or
unresolvable module; `getattr`/`importlib` access; and shadowing (a local
name that rebinds the imported name) is not tracked, so a rebound name is
still checked against its original import. (3) dependency declarations,
on by default, skipped with
`--no-deps`: per package root, shells out to a pinned deptry
(`uv run --directory <pkg> --with deptry==<version> --frozen deptry .
--known-first-party <name> --json-output <tmp>`, one `--known-first-party`
per top-level package under `<pkg>` or `<pkg>/src`, merged with any
`[tool.deptry] known_first_party` already in the package's pyproject.toml
since the command line would otherwise replace it, nested package roots
excluded via `--extend-exclude`), needs `uv` on PATH (exit 2 if missing, never a
silent skip); the first run creates a `.venv` per scanned package and
downloads its dependencies. A package is skipped, not scanned, if it
declares no runtime dependencies or has no uv.lock of its own; any other
deptry failure, or unparseable JSON, is a finding-level failure. (4) and
(5) structure limits, fixed constants, no config, no baseline: at most 500
physical lines per file, 10 entries per directory (`__init__.py`/
`__main__.py` excluded), 100 scanned files per package root, 10 public
top-level names per file, and a public top-level name that no scanned
file imports (a file no scanned file imports at all, `__init__.py` and
`__main__.py`, and a name reachable through a whole-module import, are all
exempt). (6) names carry a scope: a scanned file's stem, and a directory
holding scanned code, must have at least 2 `_`-separated words (after
stripping leading underscores), fixed, no config. Exempt files:
`__init__`, `__main__`, `conftest`, `setup`, `wsgi`, `asgi`, `_version`
(`main.py` is not exempt). Exempt directories: a package root, and `src`,
`tests`, `migrations`, `alembic`.

Every subcommand also takes a repeatable `--exclude PATTERN` (fnmatch
against the POSIX path relative to `<repo>`, matched on the path itself or
any parent directory), merged with `[tool.pymodularity] exclude` in
`<repo>`'s own pyproject.toml, never replacing it. An excluded tree is
invisible to every section, except deptry's scan of a kept package root
that contains it; an import from kept code into excluded code
then behaves like an import of code this tool can't resolve."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pymodularity._check_rules.cycle_detection import (
    count_file_level_cycles,
    find_all_cycle_findings,
)
from pymodularity._check_rules.declared_deps import (
    UvMissing,
    print_deps_findings,
    run_dependency_checks,
)
from pymodularity._check_rules.privacy_findings import (
    find_name_violations,
    find_privacy_violations,
    print_check_findings,
)
from pymodularity._check_rules.structure_limits import (
    find_structure_findings,
    print_structure_findings,
)
from pymodularity._import_parse.import_graph import build_graph
from pymodularity._import_parse.package_roots import find_package_roots


class CheckArgs(argparse.Namespace):
    # placeholder default for the static type only; argparse always
    # overwrites it before parse_args() returns
    repo: Path = Path()
    deps: bool = True
    exclude: tuple[str, ...] = ()


def add_check_parser(subparsers: argparse._SubParsersAction, exclude_help: str) -> None:
    check_parser = subparsers.add_parser(
        "check",
        help=(
            "run the import-cycle, privacy convention and dependency "
            + "declaration checks, print findings to stdout, exit 1 on "
            + "any finding"
        ),
    )
    check_parser.add_argument("repo", type=Path)
    check_parser.add_argument(
        "--no-deps",
        dest="deps",
        action="store_false",
        help="skip the dependency-declaration rule (deptry), on by default",
    )
    check_parser.add_argument("--exclude", action="append", help=exclude_help)


def run_check(repo: Path, run_deps: bool, excludes: Sequence[str] = ()) -> int:
    """`pymodularity check <repo>`: the import-cycle, privacy-convention and
    (unless `--no-deps`) dependency-declaration checks, findings printed to
    stdout, exit 1 on any finding, no views written. `excludes` makes a
    matching tree invisible to every section; an import from kept code
    into excluded code then behaves like an import of unresolvable code."""
    graph = build_graph(repo, excludes)

    cycle_findings = find_all_cycle_findings(graph.dirs_with_py, graph.edge_names)

    package_roots = find_package_roots(repo, excludes)
    privacy_violations = sorted(
        find_privacy_violations(graph.edge_names, package_roots)
        + find_privacy_violations(
            graph.attr_module_edges, package_roots, via_attribute=True
        ),
        key=lambda v: (str(v.importer), str(v.target)),
    )
    name_violations = sorted(
        find_name_violations(graph.edge_names)
        + find_name_violations(graph.attr_name_edges, via_attribute=True),
        key=lambda v: (str(v.importer), str(v.target), v.name),
    )
    distinct_file_cycles = count_file_level_cycles(graph.adj)
    any_finding = print_check_findings(
        cycle_findings, distinct_file_cycles, privacy_violations, name_violations
    )

    deps_finding = False
    if run_deps:
        deps_result = run_dependency_checks(repo, excludes)
        if isinstance(deps_result, UvMissing):
            print(
                "error: `uv` not found on PATH; the dependency-declaration "
                + "rule needs it (use --no-deps to skip)",
                file=sys.stderr,
            )
            return 2
        deps_finding = print_deps_findings(deps_result)

    structure_findings = find_structure_findings(
        repo,
        graph.py_files,
        graph.dirs_with_py,
        graph.defined_names,
        graph.registered_names,
        excludes,
    )
    structure_finding = print_structure_findings(structure_findings)

    return 1 if any_finding or deps_finding or structure_finding else 0
