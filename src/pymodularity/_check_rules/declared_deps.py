"""The dependency-declaration rule `pymodularity check` enforces: shells out
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

from pymodularity._import_parse.package_roots import find_package_roots, should_skip_dir

_DEPTRY_VERSION = "0.25.1"

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
class _DepFinding:
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
class _DepsResult:
    findings: tuple[_DepFinding, ...] = ()
    skipped: tuple[_Skipped, ...] = field(default_factory=tuple)
    failed: tuple[_Failed, ...] = field(default_factory=tuple)


def _has_runtime_dependencies(pyproject: Path) -> bool:
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    project = cast(dict[str, object], data.get("project", {}))
    deps = project.get("dependencies", [])
    return isinstance(deps, list) and len(cast(list[object], deps)) > 0


def _top_level_package_names(repo: Path, pkg: Path) -> tuple[str, ...]:
    """Top-level first-party package/module names deptry should be told
    about explicitly: deptry does not treat a hatch-autodetected src-layout
    package (no explicit `[tool.hatch.build.targets.wheel] packages`) as
    first-party on its own, so a module importing its own package is
    wrongly reported as DEP003. `pkg/src` is the base if present, the same
    src-layout check `_resolve.build_index` uses for import roots;
    otherwise pkg itself."""
    base = repo / pkg / "src"
    if not base.is_dir():
        base = repo / pkg
    if not base.is_dir():
        return ()
    names: set[str] = set()
    for entry in base.iterdir():
        if entry.name.startswith(".") or should_skip_dir(entry.name):
            continue
        if entry.is_dir() and (entry / "__init__.py").is_file():
            names.add(entry.name)
        elif entry.is_file() and entry.suffix == ".py" and entry.stem != "__init__":
            names.add(entry.stem)
    return tuple(sorted(names))


def _configured_known_first_party(pyproject: Path) -> tuple[str, ...]:
    """`[tool.deptry] known_first_party` from the package's own
    pyproject.toml. deptry's CLI `--known-first-party` replaces this
    config value rather than merging with it (deptry's own
    `read_configuration_from_pyproject_toml`: pyproject.toml overrides the
    default, the command line overrides pyproject.toml -- the command line
    always wins, it never merges), so callers must merge this in
    themselves to avoid dropping a user's own setting."""
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    tool = cast(dict[str, object], data.get("tool", {}))
    deptry_cfg = cast(dict[str, object], tool.get("deptry", {}))
    configured = deptry_cfg.get("known_first_party", [])
    if not isinstance(configured, list):
        return ()
    return tuple(str(name) for name in cast(list[object], configured))


def _known_first_party_names(repo: Path, pkg: Path, pyproject: Path) -> tuple[str, ...]:
    """Union of the autodetected top-level package names and whatever the
    package's own pyproject.toml already configures for deptry."""
    return tuple(
        sorted(
            set(_top_level_package_names(repo, pkg))
            | set(_configured_known_first_party(pyproject))
        )
    )


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


def _parse_deptry_json(raw: str, pkg: Path) -> tuple[_DepFinding, ...] | _ParseError:
    try:
        data = cast(object, json.loads(raw))
    except json.JSONDecodeError as exc:
        return _ParseError(str(exc))
    if not isinstance(data, list):
        return _ParseError("expected a JSON list")
    findings: list[_DepFinding] = []
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
            _DepFinding(
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
) -> _Skipped | _Failed | tuple[_DepFinding, ...]:
    pkg_abs = repo / pkg
    cmd = [
        "uv",
        "run",
        "--isolated",
        "--directory",
        str(pkg_abs),
        "--with",
        f"deptry=={_DEPTRY_VERSION}",
        "--frozen",
        "deptry",
        ".",
        "--no-ansi",
    ]
    for rel in _nested_exclusions(pkg, package_roots):
        pattern = "^" + re.escape(rel.as_posix()) + "(/|$)"
        cmd += ["--extend-exclude", pattern]
    for name in _known_first_party_names(repo, pkg, pkg_abs / "pyproject.toml"):
        cmd += ["--known-first-party", name]
    json_out = tmp_dir / f"{pkg.as_posix().replace('/', '__') or 'root'}.json"
    cmd += ["--json-output", str(json_out)]

    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if json_out.exists():
        parsed = _parse_deptry_json(json_out.read_text(), pkg)
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


def run_dependency_checks(
    repo: Path, excludes: Sequence[str] = ()
) -> _DepsResult | UvMissing:
    """Scans every package root under repo with deptry, one uv-managed
    environment per package. `excludes` prunes matching trees out of
    package-root discovery first, so a package root entirely under an
    excluded tree gets no deptry run at all."""
    if shutil.which("uv") is None:
        return UvMissing()

    package_roots = find_package_roots(repo, excludes)
    findings: list[_DepFinding] = []
    skipped: list[_Skipped] = []
    failed: list[_Failed] = []

    with tempfile.TemporaryDirectory(prefix="pymodularity_deps_") as tmp:
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
    return _DepsResult(
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


def print_deps_findings(result: _DepsResult) -> bool:
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
