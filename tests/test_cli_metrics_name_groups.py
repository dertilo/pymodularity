"""Tests for the "## Name groups" section of `pymodularity metrics`: the
join rules, the constant filter, the re-export rule (including the
skip-the-init-itself and multi-hop-chain fixes), and the >= 1 importer /
>= 1 def-class counting floor. Split out of test_cli_metrics.py to keep
that module under the line-count soft cap.

Uses tmp_path fixtures, same style as test_cli_metrics.py -- see that
module's docstring for the rationale.

Run: `pytest tests/test_cli_metrics_name_groups.py`.
"""

from pathlib import Path

from _helpers import run_metrics, write


def test_metrics_name_groups_reports_file_with_2_counting_groups(
    tmp_path: Path,
) -> None:
    """`mod.py` defines 2 functions, each imported by a different file and
    never joined to each other -- 2 counting groups, so `mod.py` is
    listed with both names. Catches an implementation that doesn't report
    a file at all, or collapses the 2 groups into 1."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "mod.py",
        "def alpha():\n    pass\n\n\ndef beta():\n    pass\n",
    )
    write(tmp_path / "user_a.py", "from mod import alpha\n")
    write(tmp_path / "user_b.py", "from mod import beta\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "- mod.py" in section
    assert "names: alpha" in section
    assert "names: beta" in section


def test_metrics_name_groups_co_import_joins_names(tmp_path: Path) -> None:
    """`mod.py` defines 3 functions; one importer imports `alpha` and
    `beta` together, joining them into 1 group, while `gamma` is imported
    separately by another file -- 2 counting groups (`{alpha, beta}`,
    `{gamma}`), not 3. Catches an implementation that doesn't join
    co-imported names at all."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "mod.py",
        "def alpha():\n    pass\n\n\ndef beta():\n    pass\n\n\ndef gamma():\n    pass\n",
    )
    write(tmp_path / "user_ab.py", "from mod import alpha, beta\n")
    write(tmp_path / "user_g.py", "from mod import gamma\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "names: alpha, beta" in section
    assert "names: gamma" in section


def test_metrics_name_groups_internal_reference_joins_names(tmp_path: Path) -> None:
    """`mod.py` defines `helper` and `user_of_helper`, the latter calling
    the former in its body; both are imported by the same set of files
    (so without the reference join they'd each form their own group) --
    the reference join must merge them into 1 group, leaving only
    `other` as a second, separate group. Catches an implementation that
    only joins names on co-import, never on an internal reference."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "mod.py",
        "def helper():\n    pass\n\n\ndef user_of_helper():\n    helper()\n\n\n"
        + "def other():\n    pass\n",
    )
    write(tmp_path / "user_h.py", "from mod import helper\n")
    write(tmp_path / "user_u.py", "from mod import user_of_helper\n")
    write(tmp_path / "user_o.py", "from mod import other\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "names: helper, user_of_helper" in section
    assert "names: other" in section


def test_metrics_name_groups_constants_only_group_does_not_count(
    tmp_path: Path,
) -> None:
    """`mod.py` defines 2 functions (each its own group) plus 2 constants
    imported together by a third file, joining into a constants-only
    group with no def/class name -- that third group must not count, so
    `mod.py`'s 2 real function groups are still what gets listed, and the
    constants-only group never appears. Catches an implementation that
    forgets the def/async def/class floor on a group."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "mod.py",
        "def alpha():\n    pass\n\n\ndef beta():\n    pass\n\n\nX = 1\nY = 2\n",
    )
    write(tmp_path / "user_a.py", "from mod import alpha\n")
    write(tmp_path / "user_b.py", "from mod import beta\n")
    write(tmp_path / "user_xy.py", "from mod import X, Y\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "names: alpha" in section
    assert "names: beta" in section
    assert "names: X" not in section
    assert "names: Y" not in section
    assert "names: X, Y" not in section


def test_metrics_name_groups_reexport_through_init_joins(tmp_path: Path) -> None:
    """`pkg/real.py` defines `alpha` and `beta`. `pkg/__init__.py`
    re-exports `alpha` (`from pkg.real import alpha`); one outside file
    imports `alpha` through the package (`from pkg import alpha`),
    reaching `real.py` only through `__init__.py`, and `beta` directly
    from `real.py` in the same file. Without the re-export fix the
    `alpha` import counts against `__init__.py`, not `real.py`, so the
    co-import join never sees `alpha` and `beta` together, and `real.py`
    is a false positive with 2 groups; with the fix both counts against
    `real.py`, the co-import rule joins them into 1 group, and `real.py`
    has only 1 counting group, so it's not listed at all. Catches an
    implementation that attributes a re-exported import to the
    `__init__.py` instead of following it to the defining file."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "pkg/real.py",
        "def alpha():\n    pass\n\n\ndef beta():\n    pass\n",
    )
    write(tmp_path / "pkg/__init__.py", "from pkg.real import alpha\n")
    write(tmp_path / "user_both.py", "from pkg import alpha\nfrom pkg.real import beta\n")
    write(
        tmp_path / "control.py",
        "def gamma():\n    pass\n\n\ndef delta():\n    pass\n",
    )
    write(tmp_path / "user_gamma.py", "from control import gamma\n")
    write(tmp_path / "user_delta.py", "from control import delta\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "pkg/real.py" not in section
    assert "- control.py" in section
    assert "names: gamma" in section
    assert "names: delta" in section


def test_metrics_name_groups_reexport_chain_of_two_inits_joins(
    tmp_path: Path,
) -> None:
    """`pkg/sub/mod.py` defines `alpha` and `beta`. `pkg/sub/__init__.py`
    re-exports `alpha` from `mod.py`, and `pkg/__init__.py` re-exports
    `alpha` from `pkg/sub/__init__.py` in turn: a 2-hop chain. One outside
    file imports `alpha` through the top package (`from pkg import
    alpha`), reaching `mod.py` only through both `__init__.py` files, and
    `beta` directly from `mod.py` in the same file. With the chain
    followed to its end, both count against `mod.py`, the co-import rule
    joins them, and `mod.py` has only 1 counting group. Catches an
    implementation that follows only one `__init__.py` hop."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "pkg/sub/mod.py",
        "def alpha():\n    pass\n\n\ndef beta():\n    pass\n",
    )
    write(tmp_path / "pkg/sub/__init__.py", "from pkg.sub.mod import alpha\n")
    write(tmp_path / "pkg/__init__.py", "from pkg.sub import alpha\n")
    write(
        tmp_path / "user_both.py",
        "from pkg import alpha\nfrom pkg.sub.mod import beta\n",
    )
    write(
        tmp_path / "control.py",
        "def gamma():\n    pass\n\n\ndef delta():\n    pass\n",
    )
    write(tmp_path / "user_gamma.py", "from control import gamma\n")
    write(tmp_path / "user_delta.py", "from control import delta\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "pkg/sub/mod.py" not in section
    assert "- control.py" in section
    assert "names: gamma" in section
    assert "names: delta" in section


def test_metrics_name_groups_init_reexport_importer_not_double_counted(
    tmp_path: Path,
) -> None:
    """`pkg/__init__.py` re-exports both `alpha` and `beta` from
    `pkg/m.py` (`from pkg.m import a, b` style, here `alpha, beta`).
    Without fix 1, that `__init__.py -> m.py` edge itself counts as an
    importer joining `alpha` and `beta` together, so `m.py` never shows
    2 separate groups even though `user_a.py` and `user_b.py` import
    them separately through the package. With fix 1, the `__init__.py`
    edge is excluded from `importers_by_target`, so `m.py` is listed
    with `{alpha}` importer `user_a.py` and `{beta}` importer
    `user_b.py`. Catches an implementation that counts a re-exporting
    `__init__.py` as an importer of the names it re-exports."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "pkg/m.py",
        "def alpha():\n    pass\n\n\ndef beta():\n    pass\n",
    )
    write(tmp_path / "pkg/__init__.py", "from pkg.m import alpha, beta\n")
    write(tmp_path / "user_a.py", "from pkg import alpha\n")
    write(tmp_path / "user_b.py", "from pkg import beta\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "- pkg/m.py" in section
    assert "names: alpha\n    importers: user_a.py" in section
    assert "names: beta\n    importers: user_b.py" in section


def test_metrics_name_groups_attr_edge_joins_through_init_reexport(
    tmp_path: Path,
) -> None:
    """Same re-export shape as above, but `user_a.py` and `user_b.py`
    reach `alpha`/`beta` by attribute access (`import pkg; pkg.alpha()`)
    rather than a `from` import. The attribute-edge loop in
    `_resolved_importers_by_target` must skip the `__init__.py` edge the
    same way the name-edge loop does. Catches a fix to loop 1 (name
    edges) that misses loop 2 (attribute edges)."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "pkg/m.py",
        "def alpha():\n    pass\n\n\ndef beta():\n    pass\n",
    )
    write(tmp_path / "pkg/__init__.py", "from pkg.m import alpha, beta\n")
    write(tmp_path / "user_a.py", "import pkg\npkg.alpha()\n")
    write(tmp_path / "user_b.py", "import pkg\npkg.beta()\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "- pkg/m.py" in section
    assert "names: alpha\n    importers: user_a.py" in section
    assert "names: beta\n    importers: user_b.py" in section


def test_metrics_name_groups_parameter_does_not_join_same_named_top_level(
    tmp_path: Path,
) -> None:
    """`mod.py` defines `def a(b): return b` and `def b(): pass`, each
    imported by a different file. The parameter `b` inside `a`'s body is
    a local name, not a reference to the top-level `b`, so `a` and `b`
    must stay 2 separate groups. Catches an implementation that doesn't
    subtract a def's own parameters before intersecting its referenced
    identifiers with the file's defined names."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "mod.py",
        "def a(b):\n    return b\n\n\ndef b():\n    pass\n",
    )
    write(tmp_path / "user_a.py", "from mod import a\n")
    write(tmp_path / "user_b.py", "from mod import b\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "- mod.py" in section
    assert "names: a\n    importers: user_a.py" in section
    assert "names: b\n    importers: user_b.py" in section


def test_metrics_name_groups_global_statement_does_not_shadow_module_name(
    tmp_path: Path,
) -> None:
    """`mod.py` has a module-level `_cache` plus a getter that assigns it
    through `global _cache` and a closer that clears it the same way,
    each imported by its own file. `_cache` is the module global, not a
    name local to either def, so `get_cache` and `close_cache` must join
    through it into 1 group, below the >= 2 counting-groups floor -- the
    file must not be listed. Catches an implementation that treats a name
    reassigned via `global` inside a def as that def's own local name,
    which would split the 2 defs into 2 separate groups and wrongly list
    the file."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "mod.py",
        "_cache = None\n\n\n"
        "def get_cache():\n    global _cache\n    if _cache is None:\n"
        "        _cache = object()\n    return _cache\n\n\n"
        "def close_cache():\n    global _cache\n    _cache = None\n",
    )
    write(tmp_path / "user_get.py", "from mod import get_cache\n")
    write(tmp_path / "user_close.py", "from mod import close_cache\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "mod.py" not in section


def test_metrics_name_groups_name_under_if_try_joins(tmp_path: Path) -> None:
    """`mod.py` defines `alpha` under a top-level `try`/`except` (a
    conditional-import-style fallback def) and `beta` under a top-level
    `if`. `user_h.py` imports both together, joining them into 1 group,
    while `gamma` at plain top level is imported separately -- 2
    counting groups. Catches an implementation whose name walker misses
    names bound inside `if`/`try` branches."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "mod.py",
        "try:\n    def alpha():\n        pass\nexcept ImportError:\n    pass\n\n\n"
        + "if True:\n    def beta():\n        pass\n\n\ndef gamma():\n    pass\n",
    )
    write(tmp_path / "user_h.py", "from mod import alpha, beta\n")
    write(tmp_path / "user_g.py", "from mod import gamma\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "names: alpha, beta" in section
    assert "names: gamma" in section


def test_metrics_name_groups_annassign_name_counts(tmp_path: Path) -> None:
    """`mod.py` defines `alpha` as an annotated assignment
    (`alpha: int = 1`) referencing `beta` in its right-hand side, plus a
    plain function `gamma` imported separately. The reference join must
    merge `alpha` and `beta`, leaving `gamma` as its own group. Catches
    an implementation whose name walker misses `AnnAssign` targets."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "mod.py",
        "def beta():\n    pass\n\n\nalpha: int = beta()\n\n\ndef gamma():\n    pass\n",
    )
    write(tmp_path / "user_ab.py", "from mod import alpha, beta\n")
    write(tmp_path / "user_g.py", "from mod import gamma\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "names: alpha, beta" in section
    assert "names: gamma" in section


def test_metrics_name_groups_group_without_importer_is_dropped(
    tmp_path: Path,
) -> None:
    """`mod.py` defines `alpha`, `beta` (each imported by its own file,
    2 counting groups) and `orphan`, a function nothing in the repo
    imports. `orphan` forms its own component but has no importer, so
    it must not count toward the >= 2 floor and must not appear in the
    listing at all. Catches an implementation that drops the >= 1
    importer requirement from the counting rule."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "ng"\nversion = "0"\n')
    write(
        tmp_path / "mod.py",
        "def alpha():\n    pass\n\n\ndef beta():\n    pass\n\n\ndef orphan():\n    pass\n",
    )
    write(tmp_path / "user_a.py", "from mod import alpha\n")
    write(tmp_path / "user_b.py", "from mod import beta\n")

    result = run_metrics(tmp_path)

    assert result.returncode == 0
    section = result.stdout.split("## Name groups")[1]
    assert "names: alpha" in section
    assert "names: beta" in section
    assert "orphan" not in section
