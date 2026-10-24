"""Tests for `pymodularity`'s subcommands, run on synthetic tmp_path repos.

Uses tmp_path fixtures: the checks and metrics (cycle detection via SCCs,
the underscore-privacy convention, transitive reach, interface width) are
graph logic where a wrong implementation is easy to write and hard to spot
by eye, so each test here names a concrete wrong implementation it would
catch (see each test's docstring).

Run: `pytest tests/test_cli.py`.
"""

from pathlib import Path

from _helpers import PACKAGE_DIR, run_check, run_graph, write


def test_private_module_violation_across_sibling_package(tmp_path: Path) -> None:
    """Ported fixture: a/c/use.py reaches a/b's private module. Catches a
    wrong implementation that only checks the importer's own package root
    instead of walking up to the private component's owning directory, or
    one that misreads `__init__.py`/`__main__.py` as private (leading
    double underscore)."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "a/__init__.py", "")
    write(tmp_path / "a/b/__init__.py", "")
    write(tmp_path / "a/b/deep/__init__.py", "")
    write(tmp_path / "a/c/__init__.py", "")
    write(tmp_path / "a/b/_priv.py", "x = 1\n")
    write(tmp_path / "a/b/ok.py", "from a.b._priv import x\n")
    write(tmp_path / "a/b/deep/ok2.py", "from a.b._priv import x\n")
    write(tmp_path / "a/c/use.py", "from a.b._priv import x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "a/c/use.py -> a/b/_priv.py" in result.stdout
    # the allowed importers (same dir, and a subdirectory of it) must not
    # be reported: a do-it-always implementation (flag every import of a
    # file with an underscore part) would wrongly flag a/b/ok.py here too.
    assert "a/b/ok.py ->" not in result.stdout
    assert "a/b/deep/ok2.py ->" not in result.stdout


def test_nested_private_dir_innermost_part_wins(tmp_path: Path) -> None:
    """`_a/_b.py` has two private path parts, `_a` and `_b`. The narrowest
    (innermost) one wins: only files under `_a/` may import it, not the
    whole package. Catches an implementation that stops at the first
    (outermost) private part and treats the module as visible package-wide
    just because its containing directory is already private."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "_a/__init__.py", "")
    write(tmp_path / "_a/_b.py", "x = 1\n")
    write(tmp_path / "_a/ok.py", "from _a._b import x\n")
    write(tmp_path / "use.py", "from _a._b import x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "use.py -> _a/_b.py" in result.stdout
    assert "_a/ok.py ->" not in result.stdout


def test_private_name_fixture_tree(tmp_path: Path) -> None:
    """Rust-style private-name visibility, per a fixture tree:
    `a/__init__.py` defines `_x`, `a/b/__init__.py` defines `_y`,
    `a/b/ok.py` defines `_z`. Catches an implementation that only checks
    private modules (whole files) and ignores private names imported out
    of a regular file or package, or one that wrongly flags the allowed
    descendant/self-package imports."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "a/__init__.py", "_x = 1\n")
    write(tmp_path / "a/b/__init__.py", "_y = 1\n")
    write(tmp_path / "a/b/ok.py", "_z = 1\n")
    write(tmp_path / "a/b/c.py", "from a import _x\nfrom a.b import _y\n")
    write(tmp_path / "a/d.py", "from a.b import _y\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    # allowed: a descendant file importing its own package's private name,
    # and a/b/c.py reaching a/b's own private name.
    assert "a/b/c.py -> a/__init__.py" not in result.stdout
    assert "a/b/c.py -> a/b/__init__.py" not in result.stdout
    # a/d.py is a's own package (sibling of a/b), allowed to reach a's
    # private name but not a/b's.
    assert "a/d.py -> a/b/__init__.py: name _y private to directory a/b" in (
        result.stdout
    )


def test_private_name_parent_reaching_down_is_a_finding(tmp_path: Path) -> None:
    """The defining package's own `__init__.py` reaching into a child
    package's private name is still a finding (parent reaching down).
    Catches an implementation that treats "reachable from repo root" or
    "reachable from the package root" as sufficient instead of requiring
    the importer be under the private name's owning directory."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "a/__init__.py", "from a.b import _y\n")
    write(tmp_path / "a/b/__init__.py", "_y = 1\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "a/__init__.py -> a/b/__init__.py: name _y private to directory a/b" in (
        result.stdout
    )


def test_private_name_in_regular_file_has_no_descendants(tmp_path: Path) -> None:
    """A private name defined in a regular (non-package) file is visible
    nowhere else at all, not even to a sibling file in the same directory
    or that directory's own `__init__.py` -- a file has no descendants.
    Catches an implementation that reuses the package-directory rule for
    single-file targets and wrongly allows same-directory imports."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "a/__init__.py", "")
    write(tmp_path / "a/b/__init__.py", "from .ok import _z\n")
    write(tmp_path / "a/b/ok.py", "_z = 1\n")
    write(tmp_path / "a/b/c.py", "from a.b.ok import _z\nfrom .ok import _z\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "a/b/c.py -> a/b/ok.py: name _z private to file a/b/ok.py" in result.stdout
    assert (
        "a/b/__init__.py -> a/b/ok.py: name _z private to file a/b/ok.py" in result.stdout
    )


def test_two_file_cycle_detected(tmp_path: Path) -> None:
    """Two files importing each other inside one directory. Catches an SCC
    computation that only looks for a mutual pair annotation but drops the
    cycle from the finding count, or an exit code that stays 0 despite a
    finding (a do-nothing implementation that always prints "0 findings")."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "cyc"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/m1.py", "from pkg.m2 import x\n")
    write(tmp_path / "pkg/m2.py", "from pkg.m1 import x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert (
        "pkg/m1.py, pkg/m2.py" in result.stdout or "pkg/m2.py, pkg/m1.py" in result.stdout
    )


def test_two_sibling_directories_cycle_detected(tmp_path: Path) -> None:
    """Two sibling directories importing each other (a directory-level
    cycle, not visible at the file level within either directory's own
    view). Catches an implementation that only checks cycles among files
    in the deepest view and misses the aggregated directory-level view."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "cyc"\nversion = "0"\n')
    write(tmp_path / "sib1/__init__.py", "from sib2 import x\n")
    write(tmp_path / "sib2/__init__.py", "from sib1 import x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "sib1, sib2" in result.stdout or "sib2, sib1" in result.stdout


def test_cycle_header_directory_only_cycle(tmp_path: Path) -> None:
    """2 directories cycle only once merged into directory nodes -- no
    pair of files imports each other directly -- so the new header must
    report 0 at file level and 1 in directory views, not a contradiction
    like the old "N finding(s) ... from 0 distinct file-level cycle(s)"
    wording. Catches a header that still conflates the two counts."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "cyc"\nversion = "0"\n')
    write(tmp_path / "dir_a/__init__.py", "")
    write(tmp_path / "dir_a/a1.py", "from dir_b.b1 import x\n")
    write(tmp_path / "dir_a/a2.py", "x = 1\n")
    write(tmp_path / "dir_b/__init__.py", "")
    write(tmp_path / "dir_b/b1.py", "x = 1\n")
    write(tmp_path / "dir_b/b2.py", "from dir_a.a2 import x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "## Cycles: 1 in directory views, 0 at file level" in result.stdout


def test_no_findings_exits_zero(tmp_path: Path) -> None:
    """A clean repo (no cycle, no private-module violation) must exit 0.
    Catches an implementation that always reports a finding regardless of
    the graph (the inverse do-it-always failure to the one above)."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "clean"\nversion = "0"\n')
    write(tmp_path / "app_pkg/__init__.py", "")
    write(tmp_path / "app_pkg/module_one.py", "x = 1\n")
    write(tmp_path / "app_pkg/module_two.py", "from app_pkg.module_one import x\n")

    result = run_check(tmp_path)

    assert result.returncode == 0
    assert "0 finding(s)" in result.stdout


def test_check_writes_no_views(tmp_path: Path) -> None:
    """`check` must not write any view files. Catches an implementation
    that still unconditionally writes to a default/cwd output directory."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "clean"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/m1.py", "x = 1\n")

    before = {p for p in tmp_path.rglob("*") if p.is_file()}
    run_check(tmp_path)
    after = {p for p in tmp_path.rglob("*") if p.is_file()}

    assert before == after


def test_graph_writes_views(tmp_path: Path) -> None:
    """`graph --out DIR` writes an index.md at the repo root and at least
    one child view. Catches a `graph` wiring that silently does nothing,
    or one that requires `--html` to write anything at all."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "g"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/m1.py", "x = 1\n")
    out_dir = tmp_path / "out"

    result = run_graph(tmp_path, out_dir)

    assert result.returncode == 0
    assert (out_dir / "index.md").is_file()
    assert (out_dir / "pkg" / "index.md").is_file()


def test_parse_deptry_json_handles_null_line() -> None:
    """A deptry JSON finding with line/column null (deptry reports this for
    some finding kinds) must parse to the "no position" case, not crash or
    silently coerce null to 0."""
    from pymodularity._check_rules.declared_deps import (
        _DepsResult,
        _parse_deptry_json,
        print_deps_findings,
    )

    raw = """
    [
      {
        "error": {"code": "DEP002", "message": "'foo' defined but never used"},
        "location": {"file": "pkg/m.py", "line": null, "column": null}
      },
      {
        "error": {"code": "DEP001", "message": "'bar' not installed"},
        "location": {"file": "pkg/n.py", "line": 3, "column": 1}
      }
    ]
    """

    parsed = _parse_deptry_json(raw, Path("pkg"))

    assert isinstance(parsed, tuple)
    findings = parsed
    assert len(findings) == 2
    assert findings[0].code == "DEP002"
    assert findings[1].code == "DEP001"

    import io
    from contextlib import redirect_stdout

    out = io.StringIO()
    with redirect_stdout(out):
        print_deps_findings(_DepsResult(findings=findings))
    printed = out.getvalue()
    assert "pkg/pkg/m.py: DEP002" in printed
    assert "pkg/pkg/n.py:3:1: DEP001" in printed


def test_deps_rule_skips_package_with_no_runtime_dependencies(tmp_path: Path) -> None:
    """A package whose pyproject.toml has no [project].dependencies must be
    skipped (not scanned, not a failure), exit 0, section present."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "nodep"\nversion = "0"\n')
    write(tmp_path / "app_pkg/__init__.py", "")
    write(tmp_path / "app_pkg/module_one.py", "x = 1\n")

    result = run_check(tmp_path)

    assert result.returncode == 0
    assert "## Dependency declarations: 0 finding(s)" in result.stdout
    assert "skipped (declares no dependencies)" in result.stdout


def test_no_deps_flag_omits_dependency_section(tmp_path: Path) -> None:
    """`--no-deps` must skip the rule entirely: no section at all, not an
    empty one."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "nodep"\nversion = "0"\n')
    write(tmp_path / "app_pkg/__init__.py", "")
    write(tmp_path / "app_pkg/module_one.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Dependency declarations" not in result.stdout


def test_deps_rule_exits_2_when_uv_missing(tmp_path: Path) -> None:
    """`uv` missing from PATH must be a hard exit 2 with a one-line error,
    never a silent skip of the whole rule."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "needsdep"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/m1.py", "x = 1\n")

    stub_bin = tmp_path / "_stub_bin"
    stub_bin.mkdir()
    env = {
        "PATH": str(stub_bin),
        "PYTHONPATH": str(PACKAGE_DIR),
    }

    result = run_check(tmp_path, env=env)

    assert result.returncode == 2
    assert "uv" in result.stderr.lower()
