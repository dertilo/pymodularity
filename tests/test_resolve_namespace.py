"""Regression tests for the mcp-poi/ds1_client layout: a nested package
(its own pyproject.toml, [project] table) sitting inside a directory of
the same dotted name with no __init__.py. Reproduces two dogfooding bugs:

1. resolution picking the namespace-portion directory over the regular
   package nested inside it (Python prefers the regular package).
2. a directory target equal to its own package root crashing
   `module_comp`/`_visibility_scope` with an IndexError (it assumed every
   target ends in `.py`).

Run: `pytest tests/test_resolve_namespace.py`.
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


def _write_ds1_client_layout(tmp_path: Path) -> None:
    write(tmp_path / "pyproject.toml", '[project]\nname = "mcp-poi"\nversion = "0"\n')
    write(tmp_path / "db.py", "import ds1_client\n")
    write(tmp_path / "ds1_api.py", "import ds1_client\n")
    write(
        tmp_path / "ds1_client/pyproject.toml",
        '[project]\nname = "ds1-client"\nversion = "0"\n',
    )
    write(tmp_path / "ds1_client/ds1_client/__init__.py", "x = 1\n")
    write(tmp_path / "ds1_client/ds1_client/api.py", "x = 1\n")


def test_resolution_prefers_regular_package_over_namespace_portion(
    tmp_path: Path,
) -> None:
    """A wrong implementation (resolving by ancestor-depth alone, ignoring
    whether a candidate is a real package) would resolve `import
    ds1_client` to the directory `ds1_client/` itself (no `__init__.py`),
    not the regular package nested inside it. That directory has no
    private names, so the bug would hide as 0 findings; assert on the
    resolved edge directly via a privacy violation that only fires if the
    edge lands on the inner package."""
    _write_ds1_client_layout(tmp_path)
    write(tmp_path / "ds1_client/ds1_client/_priv.py", "x = 1\n")
    write(tmp_path / "db.py", "from ds1_client._priv import x\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode == 1, result.stdout
    assert "db.py -> ds1_client/ds1_client/_priv.py" in result.stdout, result.stdout


def test_namespace_package_dir_target_does_not_crash(tmp_path: Path) -> None:
    """A directory target equal to its own package root used to crash
    `module_comp` with an IndexError (it assumed every target ends in
    `.py`, stripping 3 chars off a directory name). `ds1_client/` (the
    outer namespace-portion directory) is exactly that: the package root
    for the nested `ds1-client` project, with no `__init__.py` of its own.
    Also covers a private namespace directory (`_ns/`) nested one level
    further in: it must restrict visibility like a private package, not
    crash either."""
    _write_ds1_client_layout(tmp_path)
    write(tmp_path / "ds1_client/ds1_client/_ns/mod.py", "y = 1\n")
    write(tmp_path / "ds1_client/ds1_client/use_ns.py", "import ds1_client._ns.mod\n")
    write(tmp_path / "ds1_api.py", "import ds1_client._ns.mod\n")

    result = run_check(tmp_path, extra_args=("--no-deps",))

    assert result.returncode != 0, result.stdout
    expected = (
        "ds1_api.py -> ds1_client/ds1_client/_ns/mod.py: allowed only inside "
        "ds1_client/ds1_client"
    )
    assert expected in result.stdout, result.stdout
    assert not any(
        "use_ns" in line and "allowed only inside" in line
        for line in result.stdout.splitlines()
    ), result.stdout
