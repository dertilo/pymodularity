"""Golden test for `tests/showcase/`: a static monorepo that demonstrates
every `check` rule and the known gaps. See `tests/showcase/README.md` for
what each case is and why it should (or should not) fire.

Run: `pytest tests/test_showcase.py`.
"""

import difflib
import subprocess
import sys
from pathlib import Path

PACKAGE_DIR = Path(__file__).parent.parent / "src"
SHOWCASE_DIR = Path(__file__).parent / "showcase"


def _diff(label: str, expected: str, actual: str) -> str:
    return "\n".join(
        difflib.unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile=f"expected_{label}.txt",
            tofile=f"actual {label}",
            lineterm="",
        )
    )


def test_showcase_matches_golden_output() -> None:
    """`pymodularity check --no-deps tests/showcase` must reproduce the
    exact findings and warnings committed alongside it: one case ID per
    finding, no stray interference between cases, exit code 1."""
    result = subprocess.run(
        [sys.executable, "-m", "pymodularity", "check", str(SHOWCASE_DIR), "--no-deps"],
        capture_output=True,
        text=True,
        check=False,
        cwd=PACKAGE_DIR,
    )

    expected_stdout = (SHOWCASE_DIR / "expected_check.txt").read_text(encoding="utf-8")
    expected_stderr = (SHOWCASE_DIR / "expected_stderr.txt").read_text(encoding="utf-8")

    stdout_diff = _diff("check", expected_stdout, result.stdout)
    assert result.stdout == expected_stdout, f"stdout mismatch:\n{stdout_diff}"

    stderr_diff = _diff("stderr", expected_stderr, result.stderr)
    assert result.stderr == expected_stderr, f"stderr mismatch:\n{stderr_diff}"

    assert result.returncode == 1
