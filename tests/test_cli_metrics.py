"""Tests for `pymodularity metrics`: reach, interface width, file/directory
score, exposure share, entry files, wrapper-directory skipping, and the
importer distance table. Split out of test_cli.py to keep that module
under the line-count soft cap.

Uses tmp_path fixtures, same style as test_cli.py -- see that module's
docstring for the rationale.

Run: `pytest tests/test_cli_metrics.py`.
"""

from pathlib import Path

from _helpers import run_graph, run_metrics, write


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


def test_metrics_subdirectory_file_imported_by_parent_is_not_listed(
    tmp_path: Path,
) -> None:
    """`pkg/parent.py` imports `pkg/sub/child.py`, its only importer.
    up(parent's dir, child's dir) = 0: a parent reaching into its own
    subdirectory is a down-step, free by definition. now = 0, so
    `child.py` has no row at all (gain is only defined for now > 0).
    Catches an implementation that counts a down-step like an up-step,
    which would give child.py a positive `now` and list it."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "pkgp"\nversion = "0"\n')
    write(tmp_path / "pkg/parent.py", "import pkg.sub.child\n")
    write(tmp_path / "pkg/sub/child.py", "x = 1\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Importer distance")[1]
    assert "child.py" not in section


def test_metrics_helpers_file_used_only_by_sibling_routes_is_listed(
    tmp_path: Path,
) -> None:
    """`pkg/helpers/h.py` has 2 importers, both in the sibling directory
    `pkg/routes`, none in `pkg/helpers` itself. now = 2 (1 up-step each,
    siblings under `pkg`); the best directory within the file's own
    distribution is `pkg/routes` itself, cost 0 -> gain = 2, rel = 100%.
    Catches an implementation that only looks at ancestor directories of
    the file's own directory as candidates, missing a sibling subtree."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "pkgh"\nversion = "0"\n')
    write(tmp_path / "pkg/helpers/h.py", "x = 1\n")
    write(tmp_path / "pkg/routes/r1.py", "import pkg.helpers.h\n")
    write(tmp_path / "pkg/routes/r2.py", "import pkg.helpers.h\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Importer distance")[1]
    assert (
        "| pkg/helpers/h.py | 2 | 2 | pkg/routes | 0 | 2 | 100% | pkg/routes:2 |"
        in section
    )


def test_metrics_best_dir_never_crosses_into_another_distribution(
    tmp_path: Path,
) -> None:
    """`libs/target.py` (part of the repo-root distribution, no closer
    pyproject.toml) has 3 importers in `pkg_b/users` (a separate
    distribution, its own pyproject.toml) and 1 in `libs/local` (same
    distribution as the file). `pkg_b/users` is the biggest importer
    group, but candidates are restricted to the file's own distribution,
    so best dir must stay inside the root distribution (`libs/local`)
    even though moving into `pkg_b/users` would cost less. By hand:
    now = 3*up(pkg_b/users, libs) + 1*up(libs/local, libs) = 3*2 + 1*1 = 7;
    cost(libs/local) = 3*up(pkg_b/users, libs/local) + 0 = 3*2 = 6, the
    best candidate inside the root distribution -> gain=1, rel=14%.
    Catches an implementation that picks the directory with the lowest
    absolute cost repo-wide, ignoring the distribution boundary."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "root"\nversion = "0"\n')
    write(tmp_path / "pkg_b/pyproject.toml", '[project]\nname = "pkgb"\nversion = "0"\n')
    write(tmp_path / "libs/target.py", "x = 1\n")
    write(tmp_path / "libs/local/l1.py", "import libs.target\n")
    write(tmp_path / "pkg_b/users/u1.py", "import libs.target\n")
    write(tmp_path / "pkg_b/users/u2.py", "import libs.target\n")
    write(tmp_path / "pkg_b/users/u3.py", "import libs.target\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Importer distance")[1]
    assert (
        "| libs/target.py | 4 | 7 | libs/local | 6 | 1 | 14% | "
        + "pkg_b/users:3, libs/local:1 |"
        in section
    )
    assert "| libs/target.py | 4 | 7 | pkg_b" not in section
