"""Tests for the structure-limit rules (file size, directory fan-out,
package size, file width), run on synthetic tmp_path repos, offline.

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
    implementation that forgets to exempt decorator-registered names from
    the width count."""
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
