"""Shared process-running helpers for the `pymodularity` CLI tests.

Split out of `test_cli.py` so `test_cli_attr_form.py` and
`test_cli_metrics.py` can import them without relying on
`test_cli` being importable as a top-level module (breaks under
`--import-mode=importlib`).
"""

import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent.parent / "src"


def run_check(
    repo: Path,
    extra_args: tuple[str, ...] = (),
    env: Mapping[str, str] = os.environ,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pymodularity", "check", str(repo), *extra_args],
        capture_output=True,
        text=True,
        check=False,
        cwd=PACKAGE_DIR,
        env=env,
    )


def run_metrics(
    repo: Path, extra_args: tuple[str, ...] = ()
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pymodularity", "metrics", str(repo), *extra_args],
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
        "pymodularity",
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
