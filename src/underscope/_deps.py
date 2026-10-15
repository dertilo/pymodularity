"""The dependency-declaration rule `underscope check` enforces: shells out
to deptry, pinned to one exact version, once per package root (a directory
with a pyproject.toml [project] table found by `_parse.packages`), via
`uv run --directory <pkg> --with deptry==<pin> --frozen deptry . --json-output
<tmp>`. A package is skipped (not a failure) if it declares no runtime
dependencies, or has no uv.lock of its own (uv run --frozen fails with exit
2 and "Unable to find lockfile"). Any other deptry failure, or JSON that
does not parse, is a finding-level failure. `uv` missing from PATH is a
hard error (exit 2), never a silent skip.

Imported only by `cli`; must not import `_check`, `_metrics`, `_scores` or
`_views` (this rule is independent of the import-graph checks)."""

import json
import re
import shutil
import subprocess
import tempfile
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from underscope._parse.packages import find_package_roots

DEPTRY_VERSION = "0.25.1"

_NO_LOCKFILE_MARKER = "Unable to find lockfile"


@dataclass(frozen=True, slots=True)
class _KnownPosition:
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class _UnknownPosition:
    """deptry reports a finding with no file position (line/column null in
    its JSON)."""


_Position = _KnownPosition | _UnknownPosition


@dataclass(frozen=True, slots=True)
class DepFinding:
    """One deptry finding, scoped to the package it was found in."""

    pkg: Path
    file: str
    position: _Position
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class _Skipped:
    pkg: Path
    reason: str


@dataclass(frozen=True, slots=True)
class _Failed:
    """deptry failed to run on a package, or its JSON output did not
    parse."""

    pkg: Path
    stderr_tail: str


@dataclass(frozen=True, slots=True)
class DepsResult:
    findings: tuple[DepFinding, ...] = ()
    skipped: tuple[_Skipped, ...] = field(default_factory=tuple)
    failed: tuple[_Failed, ...] = field(default_factory=tuple)


def _has_runtime_dependencies(pyproject: Path) -> bool:
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    project = cast(dict[str, object], data.get("project", {}))
    deps = project.get("dependencies", [])
    return isinstance(deps, list) and len(cast(list[object], deps)) > 0


def _nested_exclusions(pkg: Path, package_roots: Sequence[Path]) -> tuple[Path, ...]:
    """Subdirectories of pkg that are themselves package roots, relative to
    pkg, so a package's own scan does not re-scan a nested package."""
    nested: list[Path] = []
    for other in package_roots:
        if other == pkg:
            continue
        try:
            rel = other.relative_to(pkg)
        except ValueError:
            continue
        nested.append(rel)
    return tuple(nested)


def _position_from(line: object, column: object) -> _Position:
    if isinstance(line, int) and isinstance(column, int):
        return _KnownPosition(line, column)
    return _UnknownPosition()


@dataclass(frozen=True, slots=True)
class _ParseError:
    """deptry's JSON output did not have the shape this parser expects."""

    message: str


def parse_deptry_json(raw: str, pkg: Path) -> tuple[DepFinding, ...] | _ParseError:
    try:
        data = cast(object, json.loads(raw))
    except json.JSONDecodeError as exc:
        return _ParseError(str(exc))
    if not isinstance(data, list):
        return _ParseError("expected a JSON list")
    findings: list[DepFinding] = []
    for item in cast(list[object], data):
        if not isinstance(item, dict):
            return _ParseError("expected a JSON object per finding")
        item = cast(dict[str, object], item)
        err = cast(dict[str, object], item.get("error", {}))
        loc = cast(dict[str, object], item.get("location", {}))
        code = err.get("code", "?")
        message = err.get("message", "")
        file = loc.get("file", "?")
        if (
            not isinstance(code, str)
            or not isinstance(message, str)
            or not isinstance(file, str)
        ):
            return _ParseError("expected str fields in error/location")
        findings.append(
            DepFinding(
                pkg=pkg,
                file=file,
                position=_position_from(loc.get("line"), loc.get("column")),
                code=code,
                message=message,
            )
        )
    return tuple(findings)


def _run_deptry_on(
    repo: Path, pkg: Path, package_roots: Sequence[Path], tmp_dir: Path
) -> _Skipped | _Failed | tuple[DepFinding, ...]:
    pkg_abs = repo / pkg
    cmd = [
        "uv",
        "run",
        "--directory",
        str(pkg_abs),
        "--with",
        f"deptry=={DEPTRY_VERSION}",
        "--frozen",
        "deptry",
        ".",
        "--no-ansi",
    ]
    for rel in _nested_exclusions(pkg, package_roots):
        pattern = "^" + re.escape(rel.as_posix()) + "(/|$)"
        cmd += ["--extend-exclude", pattern]
    json_out = tmp_dir / f"{pkg.as_posix().replace('/', '__') or 'root'}.json"
    cmd += ["--json-output", str(json_out)]

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if json_out.exists():
        parsed = parse_deptry_json(json_out.read_text(), pkg)
        if isinstance(parsed, _ParseError):
            return _Failed(pkg, parsed.message)
        return parsed
    if proc.returncode == 2 and _NO_LOCKFILE_MARKER in proc.stderr:
        return _Skipped(pkg, "no uv.lock found (uv run --frozen needs one)")
    tail = "\n".join(proc.stderr.strip().splitlines()[-20:]) or "(no stderr output)"
    return _Failed(pkg, tail)


@dataclass(frozen=True, slots=True)
class UvMissing:
    """`uv` is not on PATH; the dependency-declaration rule cannot run at
    all, never a silent skip."""


def run_dependency_checks(repo: Path) -> DepsResult | UvMissing:
    """Scans every package root under repo with deptry, one uv-managed
    environment per package."""
    if shutil.which("uv") is None:
        return UvMissing()

    package_roots = find_package_roots(repo)
    findings: list[DepFinding] = []
    skipped: list[_Skipped] = []
    failed: list[_Failed] = []

    with tempfile.TemporaryDirectory(prefix="underscope_deps_") as tmp:
        tmp_dir = Path(tmp)
        for pkg in package_roots:
            pyproject = repo / pkg / "pyproject.toml"
            if not _has_runtime_dependencies(pyproject):
                skipped.append(_Skipped(pkg, "declares no dependencies"))
                continue
            outcome = _run_deptry_on(repo, pkg, package_roots, tmp_dir)
            if isinstance(outcome, _Skipped):
                skipped.append(outcome)
            elif isinstance(outcome, _Failed):
                failed.append(outcome)
            else:
                findings.extend(outcome)

    findings.sort(key=lambda f: (str(f.pkg), f.file, _sort_pos(f.position)))
    skipped.sort(key=lambda s: str(s.pkg))
    failed.sort(key=lambda f: str(f.pkg))
    return DepsResult(
        findings=tuple(findings), skipped=tuple(skipped), failed=tuple(failed)
    )


def _sort_pos(position: _Position):
    """Sort key for a position: (0, 0) for the unknown case sorts first,
    same ordering `deptry_all.py` uses. No return-type annotation here
    deliberately -- `tuple[int, int]` as a *named* return type is the
    anti-pattern `prefer-named-types-over-tuples` flags; an unlabeled
    sort-key return isn't."""
    if isinstance(position, _KnownPosition):
        return (position.line, position.column)
    return (0, 0)


def print_deps_findings(result: DepsResult) -> bool:
    """Prints the dependency-declaration section, returns True if any
    finding or failure exists (failures are finding-level, same exit
    behavior as a finding)."""
    any_finding = bool(result.findings) or bool(result.failed)

    print(f"## Dependency declarations: {len(result.findings)} finding(s)")
    for f in result.findings:
        loc = (
            f"{f.pkg}/{f.file}:{f.position.line}:{f.position.column}"
            if isinstance(f.position, _KnownPosition)
            else f"{f.pkg}/{f.file}"
        )
        print(f"{loc}: {f.code} {f.message}")
    for failure in result.failed:
        print(f"- {failure.pkg}: deptry failed to run or its output did not parse")
        print(failure.stderr_tail)
    for s in result.skipped:
        print(f"- {s.pkg}: skipped ({s.reason})")
    print()

    return any_finding
