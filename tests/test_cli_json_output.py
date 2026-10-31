"""Tests for `pymodularity check --output-format json`: the 7-key record
shape, 1 finding each from cycle/privacy/structure-cap/rule-5 sources,
a clean repo's empty array, and the exit-2 path on an uncaught exception.

Run: `pytest tests/test_cli_json_output.py`.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest
from _helpers import run_check, write

_FINDING_KEYS = {"rule", "severity", "path", "line", "col", "message", "url"}


def test_json_mode_clean_repo_prints_empty_array_exit_0(tmp_path: Path) -> None:
    """A clean repo: json mode prints exactly `[]`, nothing else, exit 0.
    Catches an implementation that still prints text headings/counts
    alongside the array, or one that forgets the empty-array case."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "clean"\nversion = "0"\n')
    write(tmp_path / "clean_pkg/__init__.py", "")
    write(tmp_path / "clean_pkg/clean_module.py", "x = 1\n")

    result = run_check(tmp_path, ("--no-deps", "--output-format", "json"))

    assert result.returncode == 0
    assert result.stdout.strip() == "[]"


def test_json_mode_cycle_privacy_and_structure_findings(tmp_path: Path) -> None:
    """One fixture producing 1 import-cycle finding, 1 private-module-import
    finding, 1 directory-too-many-entries finding and 1
    file-name-too-few-words finding, all through `--output-format json`.
    Every object carries exactly the 7 keys, and each finding's `rule`
    matches its source. Catches an implementation that mixes text-mode
    headings/counts into stdout, drops a key, or mis-maps a rule name."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    # import cycle: 2 files importing each other
    write(tmp_path / "pkg/m1.py", "from pkg.m2 import x\n")
    write(tmp_path / "pkg/m2.py", "from pkg.m1 import x\n")
    # private-module-import: pkg2/use.py reaches pkg's private module
    write(tmp_path / "pkg/_priv.py", "y = 1\n")
    write(tmp_path / "pkg2/__init__.py", "")
    write(tmp_path / "pkg2/use.py", "from pkg._priv import y\n")
    # directory-too-many-entries: 11 files in one directory
    for i in range(11):
        write(tmp_path / f"wide/file_{i}.py", "z = 1\n")
    write(tmp_path / "wide/__init__.py", "")
    # file-name-too-few-words: a 1-word stem
    write(tmp_path / "pkg/onename.py", "w = 1\n")

    result = run_check(tmp_path, ("--no-deps", "--output-format", "json"))

    assert result.returncode == 1
    records = json.loads(result.stdout)
    assert isinstance(records, list)
    assert records, "expected at least 1 finding"
    for rec in records:
        assert set(rec.keys()) == _FINDING_KEYS
        assert rec["severity"] == "error"
        assert isinstance(rec["line"], int)
        assert isinstance(rec["col"], int)

    rules = {rec["rule"] for rec in records}
    assert "import-cycle" in rules
    assert "private-module-import" in rules
    assert "directory-too-many-entries" in rules
    assert "file-name-too-few-words" in rules

    # text-mode-only wording never leaks into json stdout
    assert "Cycles:" not in result.stdout
    assert "Structure limits:" not in result.stdout


def test_json_mode_dependency_check_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`dependency-check-failed` when deptry itself fails on a package,
    forced cheaply by monkeypatching `_run_deptry_on` instead of needing a
    real broken deptry run. In-process (not subprocess) so `monkeypatch`
    can reach the imported function. Confirms the package path and a
    stderr tail land in the message, and the rule name is
    `dependency-check-failed` rather than crashing the whole command."""
    from pymodularity import __main__ as pymodularity_main
    from pymodularity._check_rules import declared_deps

    write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "fix"\nversion = "0"\ndependencies = ["idna"]\n',
    )
    write(tmp_path / "fix_pkg/__init__.py", "")
    write(tmp_path / "fix_pkg/m.py", "x = 1\n")

    def _fake_run_deptry_on(repo, pkg, package_roots, tmp_dir):
        return declared_deps._Failed(pkg, "boom: deptry exploded")

    # nosemgrep: ban-mocking  # boundary: deptry is a subprocess this test can't afford to run for real
    monkeypatch.setattr(declared_deps, "_run_deptry_on", _fake_run_deptry_on)
    # nosemgrep: ban-mocking  # boundary: `uv` on PATH, same external dependency as above
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    # nosemgrep: ban-mocking  # boundary: process argv, not a collaborator -- needs in-process, not subprocess, to reach the patches above
    monkeypatch.setattr(
        sys, "argv", ["pymodularity", "check", str(tmp_path), "--output-format", "json"]
    )

    exit_code = pymodularity_main.main()

    captured = capsys.readouterr()
    assert exit_code == 1
    records = json.loads(captured.out)
    failures = [r for r in records if r["rule"] == "dependency-check-failed"]
    assert len(failures) == 1
    assert "boom: deptry exploded" in failures[0]["message"]
    assert failures[0]["path"] == "."


def test_json_mode_uncaught_exception_exits_2_with_empty_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A check function raising mid-collection: exit 2, stdout stays
    empty (the array is only ever printed after every finding is
    collected), traceback goes to stderr. In-process (not subprocess) so
    `monkeypatch` can reach the imported function. Catches an
    implementation that prints partial text/json before the exception, or
    that lets the exception escape uncaught with a different exit code."""
    from pymodularity import __main__ as pymodularity_main
    from pymodularity import check_command

    write(tmp_path / "pyproject.toml", '[project]\nname = "fix"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/m.py", "x = 1\n")

    def _boom(*args, **kwargs):
        raise RuntimeError("forced failure for the exit-2 test")

    # nosemgrep: ban-mocking  # boundary: forces an exception mid-collection, no real collaborator does this on a clean repo
    monkeypatch.setattr(check_command, "find_all_cycle_findings", _boom)
    # nosemgrep: ban-mocking  # boundary: process argv, not a collaborator -- needs in-process, not subprocess, to reach the patch above
    monkeypatch.setattr(
        sys,
        "argv",
        ["pymodularity", "check", str(tmp_path), "--no-deps", "--output-format", "json"],
    )

    exit_code = pymodularity_main.main()

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "forced failure for the exit-2 test" in captured.err
