"""Tests for `underscope`'s subcommands, run on synthetic tmp_path repos.

Uses tmp_path fixtures: the checks and metrics (cycle detection via SCCs,
the underscore-privacy convention, transitive reach, interface width) are
graph logic where a wrong implementation is easy to write and hard to spot
by eye, so each test here names a concrete wrong implementation it would
catch (see each test's docstring).

Run: `pytest tests/test_cli.py`.
"""

import subprocess
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent.parent / "src"


def run_check(
    repo: Path,
    extra_args: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "underscope", "check", str(repo), *extra_args],
        capture_output=True,
        text=True,
        check=False,
        cwd=PACKAGE_DIR,
        env=env,
    )


def run_metrics(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "underscope", "metrics", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        cwd=PACKAGE_DIR,
    )


def run_graph(
    repo: Path, out_dir: Path, html: bool = False
) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        "-m",
        "underscope",
        "graph",
        str(repo),
        "--out",
        str(out_dir),
    ]
    if html:
        args.append("--html")
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        cwd=PACKAGE_DIR,
    )


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


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


def test_no_findings_exits_zero(tmp_path: Path) -> None:
    """A clean repo (no cycle, no private-module violation) must exit 0.
    Catches an implementation that always reports a finding regardless of
    the graph (the inverse do-it-always failure to the one above)."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "clean"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/m1.py", "x = 1\n")
    write(tmp_path / "pkg/m2.py", "from pkg.m1 import x\n")

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


def test_metrics_reach_on_a_chain_plus_isolated_file(tmp_path: Path) -> None:
    """`pkg/a.py -> pkg/b.py -> pkg/c.py`, plus an isolated `pkg/d.py`.
    By hand: dependents are a=0, b=1 (a), c=2 (a and b transitively),
    d=0; dependencies are a=2 (b and c), b=1 (c), c=0, d=0; N=5 (with
    `__init__.py`), 2 file-level edges. Catches an implementation that
    counts only direct edges (depth 1) instead of the transitive closure,
    or one that double-counts a node reachable through two paths."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "chain"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/a.py", "import pkg.b\n")
    write(tmp_path / "pkg/b.py", "import pkg.c\n")
    write(tmp_path / "pkg/c.py", "x = 1\n")
    write(tmp_path / "pkg/d.py", "x = 1\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    assert "N (non-test .py files): 5" in result.stdout
    assert "file-level edges: 2" in result.stdout
    assert "pkg/c.py: 2 (40.0% of N)" in result.stdout
    assert "pkg/b.py: 1 (20.0% of N)" in result.stdout
    assert "pkg/a.py: 2 (40.0% of N)" in result.stdout


def test_metrics_interface_width_excludes_internal_importers(tmp_path: Path) -> None:
    """`a/sub/mod.py` defines two names; `a/sub/user.py` (inside `a/sub`)
    imports one of them, `b/other.py` (outside `a/sub`) imports the
    other. The file's width counts both importers (2 names, 2 importers),
    but the directory's width for `a/sub` must count only the outside
    import (1 name, 1 importer) -- the internal import must not count.
    Catches an implementation that reuses the file-level importer set for
    the directory metric without filtering out importers under the
    directory itself."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "width"\nversion = "0"\n')
    write(tmp_path / "a/__init__.py", "")
    write(tmp_path / "a/sub/__init__.py", "")
    write(tmp_path / "a/sub/mod.py", "foo = 1\nbar = 2\n")
    write(tmp_path / "a/sub/user.py", "from a.sub.mod import bar\n")
    write(tmp_path / "b/__init__.py", "")
    write(tmp_path / "b/other.py", "from a.sub.mod import foo\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    assert "a/sub/mod.py: 2 names, 2 importers" in result.stdout
    assert "a/sub: 1 names, 1 importers" in result.stdout


def _score_fixture(tmp_path: Path) -> None:
    """`outer.py -> pkg/a.py -> pkg/sub/b.py`, and `pkg/other.py -> outer.py`
    closes a path back in, so `pkg` and `pkg/sub` are each reached from
    outside through only one of their inner files (`a.py`/`b.py`), never
    through their `__init__.py`."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "score"\nversion = "0"\n')
    write(tmp_path / "outer.py", "import pkg.a\n")
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/a.py", "import pkg.sub.b\n")
    write(tmp_path / "pkg/sub/__init__.py", "")
    write(tmp_path / "pkg/sub/b.py", "x = 1\n")
    write(tmp_path / "pkg/other.py", "import outer\n")


def test_metrics_file_score_is_dependents_times_dependencies(tmp_path: Path) -> None:
    """By hand on the fixture above: dependents are outer=1 (other),
    a=2 (outer, other), b=3 (a, outer, other), pkg/__init__=0,
    pkg/sub/__init__=0, other=0; dependencies are outer=2 (a, b), a=1 (b),
    b=0, pkg/__init__=0, pkg/sub/__init__=0, other=3 (outer, a, b). So
    scores are outer=2, a=2, b=0, other=0, both __init__.py=0. Catches an
    implementation that adds instead of multiplies the two counts, or one
    that scores a file using another file's counts."""
    _score_fixture(tmp_path)

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    assert "| outer.py | 1 | 2 | 2 |" in result.stdout
    assert "| pkg/a.py | 2 | 1 | 2 |" in result.stdout
    assert "| pkg/sub/b.py | 3 | 0 | 0 |" in result.stdout
    assert "| pkg/other.py | 0 | 3 | 0 |" in result.stdout
    assert "| pkg/__init__.py | 0 | 0 | 0 |" in result.stdout
    assert "| pkg/sub/__init__.py | 0 | 0 | 0 |" in result.stdout


def test_metrics_dir_score_reached_only_through_inner_file(tmp_path: Path) -> None:
    """On the same fixture: everything that can reach into `pkg` or be
    reached out of it, from outside `pkg`, funnels through `pkg/a.py`
    (dependents {outer.py, pkg/other.py}, both still inside pkg itself so
    they don't count as "outside") and `pkg/other.py` (dependencies
    {outer.py, pkg/a.py, pkg/sub/b.py}, with the pkg-internal ones
    filtered out). After filtering, only `outer.py` is external on either
    side: in = {outer.py} = 1, out = {outer.py} = 1, score = 1. `pkg/sub`
    is reached from outside through `pkg/sub/b.py` alone, and that reach
    set must include `pkg/a.py` itself (a sibling of `sub`, outside `sub`
    but inside `pkg`): in = {a.py, outer.py, other.py} = 3; `sub` reaches
    nothing outside itself, out = 0, score = 0. Catches an implementation
    that filters by the top-level directory instead of the specific
    directory being measured, or one that only follows a directory's
    `__init__.py` instead of every file inside it."""
    _score_fixture(tmp_path)

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    assert "| pkg | 1 | 1 | 1 |" in result.stdout
    assert "| pkg/sub | 3 | 0 | 0 |" in result.stdout


def test_view_children_table_carries_new_columns(tmp_path: Path) -> None:
    """The per-view children table must show the same
    dependents/dependencies/score for a file child, and in/out/score for
    a directory child, as the repo-wide `metrics` tables -- computed once
    and looked up, not recomputed from the view's own (possibly partial)
    edges. Catches an implementation that only wires the new columns into
    `metrics`'s own tables and forgets the view output."""
    _score_fixture(tmp_path)
    out_dir = tmp_path / "out"

    result = run_graph(tmp_path, out_dir)
    assert result.returncode == 0

    root_view = (out_dir / "index.md").read_text()
    assert "dependents/in | dependencies/out | score" in root_view
    assert "| pkg | 1 | 0 | 1 | 1 | 0/1 | 0 | 1 | 1 | 1 |" in root_view

    pkg_view = (out_dir / "pkg" / "index.md").read_text()
    assert "| a.py | 1 | 0 | 0 | 1 | - | 0 | 2 | 1 | 2 |" in pkg_view
    assert "| sub | 1 | 0 | 0 | 0 | 0/1 | 0 | 3 | 0 | 0 |" in pkg_view


def test_metrics_exposure_share_counts_only_names_other_files_use(
    tmp_path: Path,
) -> None:
    """lib_full.py defines 10 top-level names (9 defs + 1 bare assignment);
    an importer uses all 10 via `from ... import` -> used=10, defined=10,
    share=100%. lib_partial.py defines 10 names the same way; an importer
    uses only 2 of them -> share=20%. lib_small.py defines 9 names (just
    under the defined>=10 floor) and must be excluded from the table even
    though every name is used. pkg/__init__.py defines 10 names, all used
    by an outside file, and must still be excluded (the `__init__.py`
    carve-out). A bare `import pkg.lib_full` (no `from`) must not count
    towards 'used' on its own. Catches an implementation that counts
    imported-module edges as using every name, that forgets the
    defined>=10 floor, or that doesn't special-case `__init__.py`."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "exp"\nversion = "0"\n')
    defs10 = "\n".join(f"def n{i}():\n    pass\n" for i in range(9)) + "\nX = 1\n"
    write(tmp_path / "pkg/__init__.py", defs10)
    write(tmp_path / "pkg/lib_full.py", defs10)
    write(tmp_path / "pkg/lib_partial.py", defs10)
    write(
        tmp_path / "pkg/lib_small.py",
        "\n".join(f"def n{i}():\n    pass\n" for i in range(9)),
    )
    write(
        tmp_path / "user_full.py",
        "from pkg.lib_full import n0, n1, n2, n3, n4, n5, n6, n7, n8, X\n"
        + "import pkg.lib_full\n",
    )
    write(tmp_path / "user_partial.py", "from pkg.lib_partial import n0, n1\n")
    write(tmp_path / "user_small.py", "from pkg.lib_small import n0\n")
    write(
        tmp_path / "user_init.py",
        "from pkg import n0, n1, n2, n3, n4, n5, n6, n7, n8, X\n",
    )

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    assert "| pkg/lib_full.py | 10 | 10 | 100.0% | 1 |" in result.stdout
    assert "| pkg/lib_partial.py | 2 | 10 | 20.0% | 1 |" in result.stdout
    exposure_section = result.stdout.split("## Exposure share")[1].split(
        "## Entry files"
    )[0]
    assert "pkg/lib_small.py" not in exposure_section
    assert "pkg/__init__.py" not in exposure_section


def test_metrics_entry_files_per_directory(tmp_path: Path) -> None:
    """`pkgx` has 5 files; an outside file imports 3 of them directly
    (f1.py, f2.py, f3.py) by name, a second outside file imports one of
    those same three again -> entry=3, files=5, share=60%, distinct
    outside importers=2. A 4-file sibling `small` must be excluded by the
    >=5 files floor even though every file in it is an entry file. Catches
    an implementation that counts transitive (not direct) importers, that
    double-counts an outside file importing more than one inside file, or
    that applies the floor to entry count instead of file count."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "entry"\nversion = "0"\n')
    write(tmp_path / "pkgx/f1.py", "x = 1\n")
    write(tmp_path / "pkgx/f2.py", "x = 1\n")
    write(tmp_path / "pkgx/f3.py", "x = 1\n")
    write(tmp_path / "pkgx/f4.py", "x = 1\n")
    write(tmp_path / "pkgx/f5.py", "x = 1\n")
    write(tmp_path / "small/g1.py", "x = 1\n")
    write(tmp_path / "small/g2.py", "x = 1\n")
    write(tmp_path / "small/g3.py", "x = 1\n")
    write(tmp_path / "outsider_a.py", "import pkgx.f1\nimport pkgx.f2\nimport small.g1\n")
    write(tmp_path / "outsider_b.py", "import pkgx.f1\nimport pkgx.f3\nimport small.g2\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    assert "| pkgx | 3 | 5 | 60.0% | 2 |" in result.stdout
    assert "small" not in result.stdout.split("## Entry files")[1]


def test_metrics_skips_wrapper_directories_in_every_directory_table(
    tmp_path: Path,
) -> None:
    """`wrap` holds no .py file of its own and has exactly one child,
    `wrap/inner`, which itself holds no .py file of its own and has
    exactly one child, `wrap/inner/deep`, which holds all 5 real files.
    `wrap` and `wrap/inner` each have the same file set as `deep` and must
    be skipped in the directory score table, the directory width list, and
    the entry-files table, leaving only `wrap/inner/deep`. By hand:
    `outsider.py` directly imports `deep/a.py` and `deep/b.py` (bare
    imports, no names) -> entry=2, files=5, share=40.0%, outside
    importers=1; dependents(a)=dependents(b)=1 (outsider), c/d/e=0, so
    in={outsider}=1, out=0 (none of deep's files import anything),
    score=0; width names=2 (a, b each count as 1 bare-import name),
    importers=1. Catches an implementation that only de-duplicates one of
    the three tables, or that collapses a wrapper even when it holds a
    .py file of its own or has more than one child."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "wrap"\nversion = "0"\n')
    write(tmp_path / "wrap/inner/deep/a.py", "x = 1\n")
    write(tmp_path / "wrap/inner/deep/b.py", "x = 1\n")
    write(tmp_path / "wrap/inner/deep/c.py", "x = 1\n")
    write(tmp_path / "wrap/inner/deep/d.py", "x = 1\n")
    write(tmp_path / "wrap/inner/deep/e.py", "x = 1\n")
    write(
        tmp_path / "outsider.py",
        "import wrap.inner.deep.a\nimport wrap.inner.deep.b\n",
    )

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    dir_score_section = result.stdout.split("All directories by score")[1].split(
        "## Interface width"
    )[0]
    assert "| wrap/inner/deep | 1 | 0 | 0 |" in dir_score_section
    assert "| wrap |" not in dir_score_section
    assert "| wrap/inner |" not in dir_score_section

    width_section = result.stdout.split("Top 10 directories by name count")[1].split(
        "## Exposure share"
    )[0]
    assert "- wrap/inner/deep: 2 names, 1 importers" in width_section
    assert "- wrap:" not in width_section
    assert "- wrap/inner:" not in width_section

    entry_section = result.stdout.split("## Entry files")[1]
    assert "| wrap/inner/deep | 2 | 5 | 40.0% | 1 |" in entry_section
    assert "| wrap |" not in entry_section
    assert "| wrap/inner |" not in entry_section


def test_metrics_misplaced_files_table(tmp_path: Path) -> None:
    """`dirA/f.py` imports 3 files in `dirB` directly (`x`, `y`, `z`) and is
    itself imported by a 4th `dirB` file, `w.py` (composition-root-like),
    and has no partner in its own directory: own=0, other=4, imported
    by=1 (`w`), imports=3 (`x`, `y`, `z`) -> misplaced. `dirA/h.py` is a tie
    case: 3 own-directory partners (`g`, `i`, `j`, each importing or
    imported by `h`) and 3 other-directory partners in `dirB` (`p`, `q`,
    `r`) -> own=3, other=3, not misplaced since other must be strictly
    greater than own. Every other file in the tree has at most 1 partner
    in any single other directory (other < 3), so only `f.py` is
    misplaced: 1 of 12 files."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "pkgx"\nversion = "0"\n')
    write(
        tmp_path / "dirA/f.py",
        "import dirB.x\nimport dirB.y\nimport dirB.z\n",
    )
    write(
        tmp_path / "dirA/h.py",
        "import dirA.i\nimport dirB.p\nimport dirB.q\nimport dirB.r\n",
    )
    write(tmp_path / "dirA/g.py", "import dirA.h\n")
    write(tmp_path / "dirA/i.py", "import dirA.h\n")
    write(tmp_path / "dirA/j.py", "import dirA.h\n")
    write(tmp_path / "dirB/x.py", "x = 1\n")
    write(tmp_path / "dirB/y.py", "x = 1\n")
    write(tmp_path / "dirB/z.py", "x = 1\n")
    write(tmp_path / "dirB/w.py", "import dirA.f\n")
    write(tmp_path / "dirB/p.py", "x = 1\n")
    write(tmp_path / "dirB/q.py", "x = 1\n")
    write(tmp_path / "dirB/r.py", "x = 1\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Misplaced files")[1]
    assert "1 of 12 files misplaced." in section
    assert "| dirA/f.py | 0 | 4 | 1 | 3 | dirB |" in section
    assert "| dirA/h.py |" not in section


def test_parse_deptry_json_handles_null_line() -> None:
    """A deptry JSON finding with line/column null (deptry reports this for
    some finding kinds) must parse to the "no position" case, not crash or
    silently coerce null to 0."""
    from underscope._deps import DepsResult, parse_deptry_json, print_deps_findings

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

    parsed = parse_deptry_json(raw, Path("pkg"))

    assert isinstance(parsed, tuple)
    findings = parsed
    assert len(findings) == 2
    assert findings[0].code == "DEP002"
    assert findings[1].code == "DEP001"

    import io
    from contextlib import redirect_stdout

    out = io.StringIO()
    with redirect_stdout(out):
        print_deps_findings(DepsResult(findings=findings))
    printed = out.getvalue()
    assert "pkg/pkg/m.py: DEP002" in printed
    assert "pkg/pkg/n.py:3:1: DEP001" in printed


def test_deps_rule_skips_package_with_no_runtime_dependencies(tmp_path: Path) -> None:
    """A package whose pyproject.toml has no [project].dependencies must be
    skipped (not scanned, not a failure), exit 0, section present."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "nodep"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/m1.py", "x = 1\n")

    result = run_check(tmp_path)

    assert result.returncode == 0
    assert "## Dependency declarations: 0 finding(s)" in result.stdout
    assert "skipped (declares no dependencies)" in result.stdout


def test_no_deps_flag_omits_dependency_section(tmp_path: Path) -> None:
    """`--no-deps` must skip the rule entirely: no section at all, not an
    empty one."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "nodep"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/m1.py", "x = 1\n")

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
