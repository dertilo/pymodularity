"""Regression test for a real deptry false positive: a hatch-autodetected
src-layout package (no explicit `[tool.hatch.build.targets.wheel]
packages`) is not treated as first-party by deptry, so a module importing
its own package is reported as DEP003 ("imported but it is a transitive
dependency"). Needs a real `uv` run (a lockfile, a `.venv`, a downloaded
deptry) -- skipped if `uv` is not on PATH, same condition `_deps.py`
itself gates the whole rule on.

Run: `pytest tests/test_deps_known_first_party.py`.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from _helpers import run_check, write

_needs_uv = pytest.mark.skipif(
    shutil.which("uv") is None, reason="needs a real uv on PATH"
)


def _write_hatchling_src_layout_project(repo: Path) -> None:
    write(
        repo / "pyproject.toml",
        '[project]\nname = "mypkg"\nversion = "0.1.0"\n'
        'dependencies = ["idna"]\n\n'
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n',
    )
    write(repo / "src/mypkg/__init__.py", "")
    write(repo / "src/mypkg/main.py", "import idna\nimport mypkg\n")


def test_known_first_party_merges_pyproject_config(tmp_path: Path) -> None:
    """deptry's own CLI `--known-first-party` replaces (not merges with) a
    `[tool.deptry] known_first_party` set in pyproject.toml, so
    `_known_first_party_names` must union the two itself, in Python,
    before the command line is built. Catches an implementation that
    passes only the autodetected names and silently drops a user's own
    `known_first_party` config."""
    from pymodularity._deps import _known_first_party_names

    pyproject = tmp_path / "pyproject.toml"
    write(
        pyproject,
        '[project]\nname = "mypkg"\nversion = "0.1.0"\n\n'
        "[tool.deptry]\n"
        'known_first_party = ["configured_name"]\n',
    )
    write(tmp_path / "src/mypkg/__init__.py", "")

    names = _known_first_party_names(tmp_path, Path("."), pyproject)

    assert set(names) == {"mypkg", "configured_name"}


@_needs_uv
def test_own_package_import_is_not_dep003(tmp_path: Path) -> None:
    """A hatchling src-layout package, no explicit `packages` setting,
    importing its own top-level package must not be reported as DEP003.
    Catches an implementation that does not tell deptry about the
    autodetected src-layout package name at all."""
    _write_hatchling_src_layout_project(tmp_path)
    env = dict(os.environ)
    uv_bin = Path(shutil.which("uv") or "uv").parent
    env["PATH"] = f"{uv_bin}:{env.get('PATH', '')}"
    subprocess.run(
        ["uv", "lock"], cwd=tmp_path, capture_output=True, text=True, check=True, env=env
    )

    result = run_check(tmp_path, env=env)

    assert "DEP003" not in result.stdout, result.stdout
