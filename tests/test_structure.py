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
        [sys.executable, "-m", "pymodularity", "check", str(repo), *extra_args],
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
    write(tmp_path / "pkg" / "pyproject.toml", '[project]\nname = "pkg"\nversion = "0"\n')
    write(tmp_path / "pkg" / "__init__.py", "")
    write(tmp_path / "pkg" / "__main__.py", "")
    for i in range(10):
        write(tmp_path / "pkg" / f"module_{i}.py", "x = 1\n")

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
    """11 public top-level names (including one bound by tuple-unpacking)
    trips the 10-name limit. Catches an implementation that only counts
    def/class, missing assignment targets and tuple-unpacking."""
    names = "\n".join(f"n{i} = 1" for i in range(9))
    write(tmp_path / "wide.py", f"{names}\na, b = 1, 2\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "wide.py: 11 public names (limit 10)" in result.stdout


def test_file_width_decorated_functions_exempt(tmp_path: Path) -> None:
    """16 decorated public functions plus 1 plain undecorated one stays
    under the 10-name limit: the decorated ones don't count. Catches an
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
    names = "\n".join(f"n{i} = 1" for i in range(10))
    write(tmp_path / "wide.py", f"{names}\n\n\n@register\nclass Extra:\n    pass\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "wide.py: 11 public names (limit 10)" in result.stdout


def test_file_name_one_word_violation(tmp_path: Path) -> None:
    """`models.py`, 1 word, is a finding. Catches an implementation that
    forgets the file-name word-count rule entirely."""
    write(tmp_path / "models.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "models.py: 1 word(s) in the name (minimum 2)" in result.stdout


def test_dir_name_one_word_violation(tmp_path: Path) -> None:
    """A directory named `services`, 1 word, is a finding. Catches an
    implementation that only checks file stems, not directory names."""
    write(tmp_path / "services" / "user_accounts.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "services: 1 word(s) in the name (minimum 2)" in result.stdout


def test_file_name_leading_underscore_still_one_word(tmp_path: Path) -> None:
    """`_common.py`: the leading underscore is stripped before counting,
    so it is still 1 word, not 2. Catches an implementation that counts
    the empty string before the first `_` as a word."""
    write(tmp_path / "_common.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "_common.py: 1 word(s) in the name (minimum 2)" in result.stdout


def test_file_name_version_suffix_still_one_word(tmp_path: Path) -> None:
    """`v1.py` is 1 word: a version suffix is not a second word. Catches
    an implementation that treats a trailing digit as splitting a word."""
    write(tmp_path / "v1.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "v1.py: 1 word(s) in the name (minimum 2)" in result.stdout


def test_file_name_two_words_passes(tmp_path: Path) -> None:
    """`github_pagination.py` and `s3_client.py`, both 2 words, pass.
    Catches an implementation with an off-by-one in the word-count
    comparison."""
    write(tmp_path / "github_pagination.py", "x = 1\n")
    write(tmp_path / "s3_client.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Structure limits: 0 finding(s)" in result.stdout


def test_file_name_exempt_stems(tmp_path: Path) -> None:
    """`__init__.py`, `setup.py`, `wsgi.py`, `asgi.py`, `conftest.py` and
    `_version.py` are all exempt from the word-count rule. Catches an
    implementation that forgets one of the exempt stems."""
    for stem in ("__init__", "setup", "wsgi", "asgi", "conftest", "_version"):
        write(tmp_path / f"{stem}.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Structure limits: 0 finding(s)" in result.stdout


def test_file_name_main_py_is_not_exempt(tmp_path: Path) -> None:
    """`main.py`, unlike `__main__.py`, is not on the exempt list and is
    flagged. Catches an implementation that exempts any file named
    `main`, not only the dunder."""
    write(tmp_path / "main.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "main.py: 1 word(s) in the name (minimum 2)" in result.stdout


def test_package_root_dir_exempt(tmp_path: Path) -> None:
    """The package root directory itself, a 1-word name, is exempt:
    `pyproject.toml`'s own directory never trips the directory
    word-count rule. Catches an implementation that checks every
    directory with scanned code, including the root the package lives
    in."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "project_models.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Structure limits: 0 finding(s)" in result.stdout


def test_src_dir_exempt(tmp_path: Path) -> None:
    """A directory named `src`, 1 word, is exempt: the src-layout
    convention names it, not the author. Catches an implementation that
    treats `src` like any other 1-word directory."""
    write(tmp_path / "src" / "github_pagination.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Structure limits: 0 finding(s)" in result.stdout


def test_src_layout_import_package_exempt(tmp_path: Path) -> None:
    """`src/my_tool/`, a 1-word directory directly under `src`, is exempt:
    the src layout is how a project gets a one-word import package name.
    Catches an implementation that only exempts `src` itself, not its
    direct children."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "my-tool"\nversion = "0"\n')
    write(tmp_path / "src" / "my_tool" / "__init__.py", "")
    write(tmp_path / "src" / "my_tool" / "project_models.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Structure limits: 0 finding(s)" in result.stdout


def test_src_dir_direct_child_exempt_regardless_of_name(tmp_path: Path) -> None:
    """`src/app/`, 1 word, also passes: the exemption is "parent named
    `src`", not a check against the project name. Catches an
    implementation that only exempts the one child matching the project
    name."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "my-tool"\nversion = "0"\n')
    write(tmp_path / "src" / "app" / "__init__.py", "")
    write(tmp_path / "src" / "app" / "project_models.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 0
    assert "Structure limits: 0 finding(s)" in result.stdout


def test_flat_layout_inner_package_flagged(tmp_path: Path) -> None:
    """A flat layout, `webagent/webagent/`, has no src/ to escape through:
    the inner `webagent` directory is flagged. Catches an implementation
    that exempts any directory matching the package root's name."""
    write(tmp_path / "webagent" / "pyproject.toml", '[project]\nname = "webagent"\n')
    write(tmp_path / "webagent" / "webagent" / "__init__.py", "")
    write(tmp_path / "webagent" / "webagent" / "project_models.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "webagent/webagent: 1 word(s) in the name (minimum 2)" in result.stdout


def test_src_nested_dir_flagged(tmp_path: Path) -> None:
    """`src/pkg/sub/` is flagged: `sub`'s parent is `pkg`, not `src`, so
    the exemption applies only one level below `src`, not at any depth.
    Catches an implementation that exempts every directory under `src`
    regardless of depth."""
    write(tmp_path / "src" / "pkg" / "sub" / "project_models.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1
    assert "src/pkg/sub: 1 word(s) in the name (minimum 2)" in result.stdout
