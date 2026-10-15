"""Tests for the structure-limit rules (file size, directory fan-out,
package size, file width) and the unused-public-name rule, run on
synthetic tmp_path repos, offline.

Run: `pytest tests/test_structure.py`.
"""

import subprocess
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent.parent / "src"


def run_check(
    repo: Path, extra_args: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "underscope", "check", str(repo), *extra_args],
        capture_output=True,
        text=True,
        check=False,
        cwd=PACKAGE_DIR,
    )


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_file_size_violation(tmp_path: Path) -> None:
    """A file over 500 physical lines is a finding. Catches an
    implementation that forgets this rule entirely, or counts logical
    rather than physical lines."""
    write(tmp_path / "big.py", "x = 1\n" * 501)

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "big.py: 501 lines (limit 500)" in result.stdout


def test_dir_entries_violation(tmp_path: Path) -> None:
    """11 scanned files directly under one directory trips the 10-entry
    limit. Catches an implementation that counts files repo-wide instead
    of per directory."""
    for i in range(11):
        write(tmp_path / "pkg" / f"m{i}.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "pkg: 11 entries (limit 10)" in result.stdout


def test_dir_entries_exempts_dunder_files(tmp_path: Path) -> None:
    """`__init__.py` and `__main__.py` don't count as directory entries:
    10 regular files plus both dunders must still pass. Catches an
    implementation that counts every scanned file as an entry."""
    write(tmp_path / "pkg" / "__init__.py", "")
    write(tmp_path / "pkg" / "__main__.py", "")
    for i in range(10):
        write(tmp_path / "pkg" / f"m{i}.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Structure limits: 0 finding(s)" in result.stdout


def test_package_size_violation(tmp_path: Path) -> None:
    """A package root (pyproject.toml [project] table) with over 100
    scanned files is a finding, counted against its own root, not the
    repo root. Catches an implementation that counts files repo-wide
    instead of per nearest package root."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    for i in range(101):
        write(tmp_path / "pkg" / f"m{i}.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "repo root: 101 files (limit 100)" in result.stdout


def test_file_width_violation(tmp_path: Path) -> None:
    """16 public top-level names (including one bound by tuple-unpacking)
    trips the 15-name limit. Catches an implementation that only counts
    def/class, missing assignment targets and tuple-unpacking."""
    names = "\n".join(f"n{i} = 1" for i in range(14))
    write(tmp_path / "wide.py", f"{names}\na, b = 1, 2\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "wide.py: 16 public names (limit 15)" in result.stdout


def test_file_width_decorated_functions_exempt(tmp_path: Path) -> None:
    """16 decorated public functions plus 1 plain undecorated one stays
    under the 15-name limit: the decorated ones don't count. Catches an
    implementation that exempts decorated names from the unused-name rule
    but forgets to also exempt them from the width count."""
    handlers = "\n\n\n".join(f"@route\ndef h{i}():\n    pass" for i in range(16))
    write(
        tmp_path / "wide.py",
        f"def route(f):\n    return f\n\n\n{handlers}\n\n\ndef plain():\n    pass\n",
    )

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert "wide.py" not in [
        line for line in result.stdout.splitlines() if "public names" in line
    ]


def test_file_width_decorated_class_still_counts(tmp_path: Path) -> None:
    """A decorated top-level class is not exempt from the width count
    (only decorated functions are -- `@dataclass` sits on most models).
    Catches an implementation that exempts any decorated def/class."""
    names = "\n".join(f"n{i} = 1" for i in range(15))
    write(tmp_path / "wide.py", f"{names}\n\n\n@register\nclass Extra:\n    pass\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "wide.py: 16 public names (limit 15)" in result.stdout


def test_unused_public_name_violation(tmp_path: Path) -> None:
    """A public top-level name no scanned file imports is a finding.
    Catches an implementation that never checks usage at all."""
    write(tmp_path / "a.py", "def used():\n    pass\n\n\ndef unused():\n    pass\n")
    write(tmp_path / "b.py", "from a import used\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "a.py: unused" in result.stdout
    assert "a.py: used" not in result.stdout


def test_unused_public_name_exempt_no_importer(tmp_path: Path) -> None:
    """A file no scanned file imports at all (a script/entry point) is
    exempt entirely, even though its public names are technically
    unused. Catches an implementation that flags every public name in
    every file regardless of whether the file itself is ever imported."""
    write(tmp_path / "script.py", "def main():\n    pass\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Unused public names: 0 finding(s)" in result.stdout


def test_unused_public_name_exempt_dunder_files(tmp_path: Path) -> None:
    """`__init__.py` is exempt from the unused-public-name rule even
    though it is imported (as a package) and re-exports a name no other
    file uses by that name."""
    write(tmp_path / "pkg" / "__init__.py", "def reexported():\n    pass\n")
    write(tmp_path / "use.py", "from pkg import reexported\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Unused public names: 0 finding(s)" in result.stdout


def test_unused_public_name_exempt_whole_module_import(tmp_path: Path) -> None:
    """A file imported as a whole module (`import a.b`, an edge with no
    specific names) has all its public names treated as used, even an
    otherwise-never-named one. Catches an implementation that only
    checks whether *some* name was imported, not whether the import was
    a whole-module one."""
    write(tmp_path / "a" / "__init__.py", "")
    write(tmp_path / "a" / "b.py", "def never_named():\n    pass\n")
    write(tmp_path / "use.py", "import a.b\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Unused public names: 0 finding(s)" in result.stdout


def test_unused_public_name_tests_dont_count_as_importers(tmp_path: Path) -> None:
    """A test file is not scanned at all, so a name only reached from a
    test has zero scanned importers and falls through to the "no
    scanned file imports it" exemption -- same outcome (exempt, no
    finding) as a module no one imports, never a "used" verdict.
    Catches an implementation that walks the filesystem for importers
    instead of using the already-filtered scanned edge graph, and would
    wrongly count the test import as usage."""
    write(tmp_path / "a.py", "def only_tested():\n    pass\n")
    write(tmp_path / "test_a.py", "from a import only_tested\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Unused public names: 0 finding(s)" in result.stdout


def test_unused_public_name_decorator_registered_exempt(tmp_path: Path) -> None:
    """A decorator-registered function (e.g. a FastAPI route handler) is
    exempt from the unused-public-name rule: a framework derives its
    contract from the function's name, so renaming it with a `_` prefix
    isn't a safe fix. An undecorated unimported neighbour in the same
    file is still flagged, to catch an implementation that exempts the
    whole file instead of just registered names."""
    write(
        tmp_path / "a.py",
        "def route(f):\n    return f\n\n\n"
        + "@route\ndef handler():\n    pass\n\n\n"
        + "def unused_plain():\n    pass\n",
    )
    write(tmp_path / "b.py", "from a import route\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "a.py: handler" not in result.stdout
    assert "a.py: unused_plain" in result.stdout


def test_unused_public_name_local_only_suffix(tmp_path: Path) -> None:
    """A public Protocol used only as a field annotation in its own file
    (an `ast.Name` Load elsewhere in the file, e.g. the dataclass field's
    type) -- never imported anywhere -- gets the "used only in this
    file" suffix, not "dead", since deleting it would break the
    annotation. Catches an implementation that only checks defined vs.
    imported and ignores same-file usage."""
    write(
        tmp_path / "a.py",
        "from typing import Protocol\n\n\n"
        + "class Embedder(Protocol):\n    def embed(self, x: str) -> list[float]: ...\n\n\n"
        + "class Config:\n    embedder: Embedder\n",
    )
    write(tmp_path / "b.py", "from a import Config\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "a.py: Embedder (used only in this file: prefix with _)" in result.stdout


def test_unused_public_name_dead_suffix(tmp_path: Path) -> None:
    """A public function referenced nowhere, not even inside its own
    file, gets the "dead" suffix. Catches an implementation that always
    prints one suffix regardless of same-file usage."""
    write(tmp_path / "a.py", "def used():\n    pass\n\n\ndef dead_fn():\n    pass\n")
    write(tmp_path / "b.py", "from a import used\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "a.py: dead_fn (dead: delete it)" in result.stdout


def test_unused_public_name_decorated_class_still_a_finding(tmp_path: Path) -> None:
    """A decorated top-level class gets no exemption from the
    unused-name rule (only decorated functions do -- `@dataclass` sits
    on most models). Catches an implementation that exempts any
    decorated def/class rather than only functions."""
    write(
        tmp_path / "a.py",
        "def register(c):\n    return c\n\n\n@register\nclass Model:\n    pass\n",
    )
    write(tmp_path / "b.py", "from a import register\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "a.py: Model" in result.stdout
