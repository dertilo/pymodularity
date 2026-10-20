"""Tests for the attribute-form private-item rule (`mod._x`, `a._internal.x`):
`pymodularity check`'s detection of private access reached through module
attribute chains rather than direct imports. Split out of test_cli.py to
keep that module under the line-count soft cap.

Uses tmp_path fixtures, same style as test_cli.py -- see that module's
docstring for the rationale.

Run: `pytest tests/test_cli_attr_form.py`.
"""

from pathlib import Path

from _helpers import run_check, write


def test_attr_form_1_dotted_import_then_dotted_attribute(tmp_path: Path) -> None:
    """Form 1: `import a.b.mod` binds "a", not "a.b.mod" -- a wrong
    implementation that binds the whole dotted name to "a" would resolve
    `a.b.mod._x` against the wrong anchor and miss this finding. Also
    covers "another package" (a/c is a sibling of a/b, not a descendant)."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "a/__init__.py", "")
    write(tmp_path / "a/b/__init__.py", "")
    write(tmp_path / "a/b/mod.py", "_x = 1\n")
    write(tmp_path / "a/c/__init__.py", "")
    write(tmp_path / "a/c/use.py", "import a.b.mod\n\na.b.mod._x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout
    assert (
        "a/c/use.py -> a/b/mod.py: name _x private to file a/b/mod.py "
        + "(via attribute access)"
        in result.stdout
    )


def test_attr_form_2_import_as_then_alias_attribute(tmp_path: Path) -> None:
    """Form 2: `import a.b.mod as m` binds "m" directly to the full target,
    so `m._x` resolves against that target with no further dotted hop."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "a/__init__.py", "")
    write(tmp_path / "a/b/__init__.py", "")
    write(tmp_path / "a/b/mod.py", "_x = 1\n")
    write(tmp_path / "a/c/__init__.py", "")
    write(tmp_path / "a/c/use.py", "import a.b.mod as m\n\nm._x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout
    assert (
        "a/c/use.py -> a/b/mod.py: name _x private to file a/b/mod.py "
        + "(via attribute access)"
        in result.stdout
    )


def test_attr_form_3_from_import_submodule_then_attribute(tmp_path: Path) -> None:
    """Form 3: `from a.b import mod` binds "mod" to the submodule (not a
    name inside a.b), so `mod._x` is the attribute form of the same
    private-name rule `from a.b.mod import _x` already enforces."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "a/__init__.py", "")
    write(tmp_path / "a/b/__init__.py", "")
    write(tmp_path / "a/b/mod.py", "_x = 1\n")
    write(tmp_path / "a/c/__init__.py", "")
    write(tmp_path / "a/c/use.py", "from a.b import mod\n\nmod._x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout
    assert (
        "a/c/use.py -> a/b/mod.py: name _x private to file a/b/mod.py "
        + "(via attribute access)"
        in result.stdout
    )


def test_attr_form_4_private_module_reached_through_attribute_chain(
    tmp_path: Path,
) -> None:
    """Form 4: `import a` then `a._internal.x` -- the private-module rule
    (not the private-name rule) applies to `a._internal`, exactly as if
    `use.py` had written `from a import _internal`. Also covers the
    allowed-descendant case: a file inside `a` may do the same thing."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "a/__init__.py", "")
    write(tmp_path / "a/_internal.py", "x = 1\n")
    write(tmp_path / "a/inside.py", "import a\n\na._internal.x\n")
    write(tmp_path / "use.py", "import a\n\na._internal.x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout
    assert "use.py -> a/_internal.py: allowed only inside a (via attribute access)" in (
        result.stdout
    )
    # a/inside.py is a descendant of a/_internal.py's owner (a itself) and
    # must not be flagged.
    assert "a/inside.py ->" not in result.stdout


def test_attr_form_5_private_name_in_package_init_via_attribute(
    tmp_path: Path,
) -> None:
    """Form 5: `_x` defined in `pkg/__init__.py`, accessed as `pkg._x` --
    owned by directory `pkg` (the package rule), not by `pkg/__init__.py`
    as a single file."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "_x = 1\n")
    write(tmp_path / "pkg/inside.py", "import pkg\n\npkg._x\n")
    write(tmp_path / "use.py", "import pkg\n\npkg._x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout
    assert (
        "use.py -> pkg/__init__.py: name _x private to directory pkg "
        + "(via attribute access)"
        in result.stdout
    )
    # pkg/inside.py is under pkg itself and must not be flagged.
    assert "pkg/inside.py ->" not in result.stdout


def test_attr_form_sibling_file_denied_single_file_owner(tmp_path: Path) -> None:
    """A single-file target (not a package) has no descendants at all: even
    a sibling file in the same directory accessing `mod._y` is a finding,
    unlike the directory-owned forms 4 and 5 above."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/mod.py", "_y = 1\n")
    write(tmp_path / "pkg/sibling.py", "import pkg.mod\n\npkg.mod._y\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout
    assert (
        "pkg/sibling.py -> pkg/mod.py: name _y private to file pkg/mod.py "
        + "(via attribute access)"
        in result.stdout
    )


def test_attr_form_third_party_base_name_no_finding(tmp_path: Path) -> None:
    """A base name bound to a module `pymodularity` can't resolve inside the
    scanned repo (stdlib/third-party) must not crash and must not produce
    an attribute-form finding -- the binding is simply never recorded."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "use.py", "import os\n\nos.path._getfinalpathname\n")

    result = run_check(tmp_path)

    assert result.returncode == 0
    assert "Private-item violations: 0 finding(s)" in result.stdout


def test_attr_form_src_layout_anchor(tmp_path: Path) -> None:
    """The package root's `src/` prefix must stay in the canonical dotted
    path the attribute walk hops over -- a wrong implementation that
    anchors on the written dotted name alone (dropping "src") would resolve
    `pkg.mod._x` against the wrong file and miss this finding."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "src/pkg/__init__.py", "")
    write(tmp_path / "src/pkg/mod.py", "_x = 1\n")
    write(tmp_path / "src/other/__init__.py", "")
    write(tmp_path / "src/other/use.py", "import pkg.mod\n\npkg.mod._x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout
    assert (
        "src/other/use.py -> src/pkg/mod.py: name _x private to file "
        + "src/pkg/mod.py (via attribute access)"
        in result.stdout
    )


def test_attr_form_from_dotdot_plain_binds_name(tmp_path: Path) -> None:
    """`from .. import mod` (no module part) must bind "mod" to the
    resolved sibling submodule, the same as the absolute and single-dot
    relative forms, so `mod._x` is caught."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/mod.py", "_x = 1\n")
    write(tmp_path / "pkg/sub/__init__.py", "")
    write(tmp_path / "pkg/sub/use.py", "from .. import mod\n\nmod._x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout
    assert (
        "pkg/sub/use.py -> pkg/mod.py: name _x private to file "
        + "pkg/mod.py (via attribute access)"
        in result.stdout
    )


def test_attr_form_from_dotdot_pkg_import_mod_binds_name(tmp_path: Path) -> None:
    """`from ..pkg import mod` (a module part present) must still try
    "pkg.mod" first and bind "mod" to it, the same as the absolute branch --
    a wrong implementation that only ever resolves the grouped "pkg" form
    (`graph.py` before this fix) would leave "mod" unbound and miss this."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/mod.py", "_x = 1\n")
    write(tmp_path / "sub/__init__.py", "")
    write(tmp_path / "sub/use.py", "from ..pkg import mod\n\nmod._x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout
    assert (
        "sub/use.py -> pkg/mod.py: name _x private to file pkg/mod.py "
        + "(via attribute access)"
        in result.stdout
    )


def test_attr_form_from_import_class_binds_nothing(tmp_path: Path) -> None:
    """`from a.b import Klass` binds "Klass" to a value, not a module --
    `Klass._x` must not be mistaken for the attribute form, since "Klass"
    never enters `binding`."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "a/__init__.py", "")
    write(tmp_path / "a/b/__init__.py", "")
    write(tmp_path / "a/b/mod.py", "class Klass:\n    _x = 1\n")
    write(tmp_path / "a/c/__init__.py", "")
    write(
        tmp_path / "a/c/use.py",
        "from a.b.mod import Klass\n\nKlass._x\n",
    )

    result = run_check(tmp_path)

    assert result.returncode == 0
    assert "Private-item violations: 0 finding(s)" in result.stdout


def test_attr_form_shadowing_gives_no_finding(tmp_path: Path) -> None:
    """Option A, conservative: a local name that rebinds the imported name
    anywhere in the file (here, a function parameter named "mod") drops
    that name from `binding` for the whole file -- a false finding becomes
    a missed one."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/mod.py", "_cache = 1\n")
    write(
        tmp_path / "use.py",
        "from pkg import mod\n\n\ndef g(mod):\n    return mod._cache\n",
    )

    result = run_check(tmp_path)

    assert result.returncode == 0
    assert "Private-item violations: 0 finding(s)" in result.stdout


def test_attr_form_dedup_name_form_vs_import_form(tmp_path: Path) -> None:
    """`import pkg.mod; from pkg.mod import _x; pkg.mod._x` reports the
    same (importer, target, name) through both the import form and the
    attribute form -- the attribute-form finding is dropped, leaving
    exactly 1 finding, not 2."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/mod.py", "_x = 1\n")
    write(
        tmp_path / "use.py",
        "import pkg.mod\nfrom pkg.mod import _x\npkg.mod._x\n",
    )

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout


def test_attr_form_dedup_private_module_hops_on_own_import_path(
    tmp_path: Path,
) -> None:
    """`import pkg._internal.x; pkg._internal.x.f()` must not also flag the
    intermediate hop `pkg._internal` (it lies on the plain import's own
    dotted path) nor a second attribute-form finding for `pkg._internal.x`
    itself (the import form already reports that target) -- exactly 1
    finding, not 3."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/_internal/__init__.py", "")
    write(tmp_path / "pkg/_internal/x.py", "def f():\n    pass\n")
    write(
        tmp_path / "use.py",
        "import pkg._internal.x\n\npkg._internal.x.f()\n",
    )

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout
    assert "use.py -> pkg/_internal/x.py: allowed only inside pkg" in result.stdout


def test_attr_form_two_package_roots_both_binding_same_top_name(
    tmp_path: Path,
) -> None:
    """2 package roots each contribute a top package named "a"
    (`p1/src/a`, `p2/src/a`); `import a.b; import a.c` must keep both
    bindings for "a" -- a dict overwrite that keeps only the last import's
    anchor would drop `a.b`'s binding and miss its finding."""
    write(tmp_path / "p1/pyproject.toml", '[project]\nname = "p1"\nversion = "0"\n')
    write(tmp_path / "p1/src/a/__init__.py", "")
    write(tmp_path / "p1/src/a/b.py", "_x = 1\n")
    write(tmp_path / "p2/pyproject.toml", '[project]\nname = "p2"\nversion = "0"\n')
    write(tmp_path / "p2/src/a/__init__.py", "")
    write(tmp_path / "p2/src/a/c.py", "def f():\n    pass\n")
    write(tmp_path / "use.py", "import a.b\nimport a.c\n\na.b._x\n")

    result = run_check(tmp_path)

    assert result.returncode == 1
    assert "Private-item violations: 1 finding(s)" in result.stdout
    assert (
        "use.py -> p1/src/a/b.py: name _x private to file p1/src/a/b.py "
        + "(via attribute access)"
        in result.stdout
    )
