"""Regression test: `check` must not create or change a `.venv` inside
the scanned repo. `_run_deptry_on` used to run plain `uv run --directory
<pkg> ...`, which syncs `<pkg>/.venv` (or a workspace root's `.venv`)
into the scanned repo as a side effect. Needs a real `uv` run (a
lockfile, a downloaded deptry) -- skipped if `uv` is not on PATH, same
condition `_deps.py` itself gates the whole rule on.

Run: `pytest tests/test_deps_no_venv.py`.
"""

import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
from _helpers import run_check, write

_needs_uv = pytest.mark.skipif(
    shutil.which("uv") is None, reason="needs a real uv on PATH"
)


def _write_project(repo: Path) -> None:
    write(
        repo / "pyproject.toml",
        '[project]\nname = "mypkg"\nversion = "0.1.0"\n'
        'dependencies = ["idna"]\n\n'
        "[build-system]\n"
        'requires = ["hatchling"]\n'
        'build-backend = "hatchling.build"\n',
    )
    write(repo / "src/mypkg/__init__.py", "")
    write(repo / "src/mypkg/main.py", "import idna\n")


def _lock(repo: Path, env: Mapping[str, str]) -> None:
    subprocess.run(
        ["uv", "lock"], cwd=repo, capture_output=True, text=True, check=True, env=env
    )


def _env_with_uv_on_path() -> Mapping[str, str]:
    env = dict(os.environ)
    uv_bin = Path(shutil.which("uv") or "uv").parent
    env["PATH"] = f"{uv_bin}:{env.get('PATH', '')}"
    return env


def _installed(venv: Path) -> list[str]:
    return sorted(p.name for p in venv.glob("lib/*/site-packages/*"))


@_needs_uv
def test_check_leaves_no_venv_behind(tmp_path: Path) -> None:
    """A repo with no pre-existing `.venv` must still have none after
    `check` runs. Catches a `uv run` invocation that syncs the project's
    own environment instead of using an isolated one."""
    _write_project(tmp_path)
    env = _env_with_uv_on_path()
    _lock(tmp_path, env)

    run_check(tmp_path, env=env)

    assert not (tmp_path / ".venv").exists()


@_needs_uv
def test_check_leaves_a_preexisting_venv_unchanged(tmp_path: Path) -> None:
    """A `.venv` that already exists in the scanned repo must not be
    touched by `check`. Catches an isolated run that still falls back to
    reusing/writing into a found project environment."""
    _write_project(tmp_path)
    env = _env_with_uv_on_path()
    _lock(tmp_path, env)
    subprocess.run(
        ["uv", "venv", ".venv"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    before = _installed(tmp_path / ".venv")

    run_check(tmp_path, env=env)

    assert _installed(tmp_path / ".venv") == before
