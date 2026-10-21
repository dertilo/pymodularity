"""Tests for the `exclude` option: `[tool.pymodularity] exclude` in the
repo's own pyproject.toml and the repeatable `--exclude` CLI flag, merged
(never replaced) and matched by fnmatch against the POSIX path relative to
the repo root, on a directory or any of its ancestors.

Run: `pytest tests/test_exclude.py`.
"""

from pathlib import Path

from _helpers import run_check, run_metrics, write


def test_config_exclude_skips_a_directory_tree_in_structure_and_metrics(
    tmp_path: Path,
) -> None:
    """`[tool.pymodularity] exclude` in pyproject.toml must make a whole
    directory tree invisible to the structure checks and to `metrics`.
    Catches an implementation that reads the config but never threads it
    into the file walk."""
    write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "repo"\nversion = "0"\n\n'
        "[tool.pymodularity]\n"
        'exclude = ["vendor"]\n',
    )
    wide_body = "".join(f"n{i} = {i}\n" for i in range(11))
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "pkg/m1.py", wide_body)
    write(tmp_path / "vendor/__init__.py", "")
    write(tmp_path / "vendor/big.py", wide_body)

    check_result = run_check(tmp_path)
    assert "vendor/big.py" not in check_result.stdout
    assert "pkg/m1.py" in check_result.stdout

    metrics_result = run_metrics(tmp_path)
    assert "vendor/big.py" not in metrics_result.stdout
    assert "vendor" not in metrics_result.stdout


def test_cli_exclude_merges_with_config_exclude(tmp_path: Path) -> None:
    """A `--exclude` on the command line must add to the config's
    `exclude` list, not replace it. Catches an implementation that lets
    the CLI value win outright and silently drops the config's pattern."""
    write(
        tmp_path / "pyproject.toml",
        '[project]\nname = "repo"\nversion = "0"\n\n'
        "[tool.pymodularity]\n"
        'exclude = ["vendor"]\n',
    )
    write(tmp_path / "pkg/__init__.py", "")
    write(tmp_path / "vendor/__init__.py", "")
    write(tmp_path / "vendor/big.py", "".join(f"n{i} = {i}\n" for i in range(11)))
    write(tmp_path / "generated_client/__init__.py", "")
    write(
        tmp_path / "generated_client/big.py", "".join(f"n{i} = {i}\n" for i in range(11))
    )

    result = run_check(tmp_path, extra_args=("--exclude", "generated_client"))

    assert "vendor/big.py" not in result.stdout
    assert "generated_client/big.py" not in result.stdout


def test_nested_glob_pattern_matches_at_depth(tmp_path: Path) -> None:
    """`*/generated` must match a `generated` directory at any depth, not
    only directly under the repo root. Catches an fnmatch call against
    just the directory's basename instead of its full relative path."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "repo"\nversion = "0"\n')
    write(tmp_path / "a/b/generated/__init__.py", "")
    write(tmp_path / "a/b/generated/big.py", "".join(f"n{i} = {i}\n" for i in range(11)))
    write(tmp_path / "a/keep.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--exclude", "*/generated"))

    assert "generated/big.py" not in result.stdout
    assert "## Structure limits: 0 finding(s)" in result.stdout


def test_excluded_package_root_gets_no_deptry_run(tmp_path: Path) -> None:
    """A package root entirely under an excluded tree must not be scanned
    by deptry at all -- not skipped-and-reported, simply absent, same as
    it never existed. Catches an implementation that excludes the tree's
    files from the graph but still discovers and scans its package root."""
    write(tmp_path / "pyproject.toml", '[project]\nname = "repo"\nversion = "0"\n')
    write(tmp_path / "pkg/__init__.py", "")
    write(
        tmp_path / "vendor/pyproject.toml",
        '[project]\nname = "vendored"\nversion = "0"\ndependencies = ["idna"]\n',
    )
    write(tmp_path / "vendor/vendored/__init__.py", "")
    write(tmp_path / "vendor/vendored/m.py", "x = 1\n")

    result = run_check(tmp_path, extra_args=("--exclude", "vendor"))

    assert "vendor" not in result.stdout
