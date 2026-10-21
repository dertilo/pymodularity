"""The structure-limit rules (file size, directory fan-out, package size,
public top-level-name count per file) `pymodularity check` enforces. All
limits are fixed constants, no config, no baseline. Imported only by
`cli`."""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from pymodularity._parse.packages import find_package_roots

_MAX_FILE_LINES = 500
_MAX_DIR_ENTRIES = 10
_MAX_PACKAGE_FILES = 100
_MAX_PUBLIC_NAMES = 10

# __init__.py and __main__.py don't count as directory entries (rule 2);
# every other scanned file counts fully for every rule.
_STRUCTURAL_FILES = frozenset({"__init__.py", "__main__.py"})


def _is_public(name: str) -> bool:
    return not name.startswith("_")


@dataclass(frozen=True, slots=True)
class _FileTooLong:
    file: Path
    lines: int


@dataclass(frozen=True, slots=True)
class _DirTooManyEntries:
    directory: Path
    entries: int


@dataclass(frozen=True, slots=True)
class _PackageTooManyFiles:
    package: Path
    files: int


@dataclass(frozen=True, slots=True)
class _FileTooWide:
    file: Path
    names: int


@dataclass(frozen=True, slots=True)
class _StructureFindings:
    too_long: tuple[_FileTooLong, ...] = ()
    too_many_entries: tuple[_DirTooManyEntries, ...] = ()
    package_too_big: tuple[_PackageTooManyFiles, ...] = ()
    too_wide: tuple[_FileTooWide, ...] = ()


def _is_direct_child(child: Path, parent: Path) -> bool:
    if child == parent:
        return False
    if parent == Path("."):
        return len(child.parts) == 1
    return child.parent == parent


def _find_file_size_violations(
    repo: Path, py_files: Sequence[Path]
) -> tuple[_FileTooLong, ...]:
    out: list[_FileTooLong] = []
    for f in py_files:
        n = len(f.read_text(encoding="utf-8").splitlines())
        if n > _MAX_FILE_LINES:
            out.append(_FileTooLong(f.relative_to(repo), n))
    out.sort(key=lambda v: str(v.file))
    return tuple(out)


def _find_dir_entry_violations(
    dirs_with_py: AbstractSet[Path], py_files: Sequence[Path], repo: Path
) -> tuple[_DirTooManyEntries, ...]:
    rels = [f.relative_to(repo) for f in py_files]
    out: list[_DirTooManyEntries] = []
    for d in dirs_with_py:
        file_entries = sum(
            1 for r in rels if r.parent == d and r.name not in _STRUCTURAL_FILES
        )
        dir_entries = sum(1 for c in dirs_with_py if _is_direct_child(c, d))
        total = file_entries + dir_entries
        if total > _MAX_DIR_ENTRIES:
            out.append(_DirTooManyEntries(d, total))
    out.sort(key=lambda v: str(v.directory))
    return tuple(out)


def _owning_package(rel: Path, package_roots: Sequence[Path]) -> Path:
    for root in package_roots:
        if root != Path(".") and rel.is_relative_to(root):
            return root
    return Path(".")


def _find_package_size_violations(
    repo: Path, py_files: Sequence[Path], excludes: Sequence[str]
) -> tuple[_PackageTooManyFiles, ...]:
    package_roots = find_package_roots(repo, excludes)
    counts: dict[Path, int] = {}
    for f in py_files:
        root = _owning_package(f.relative_to(repo), package_roots)
        counts[root] = counts.get(root, 0) + 1
    out = [
        _PackageTooManyFiles(pkg, n)
        for pkg, n in counts.items()
        if n > _MAX_PACKAGE_FILES
    ]
    out.sort(key=lambda v: str(v.package))
    return tuple(out)


def _find_file_width_violations(
    defined_names: Mapping[Path, frozenset[str]],
    registered_names: Mapping[Path, frozenset[str]],
) -> tuple[_FileTooWide, ...]:
    out: list[_FileTooWide] = []
    for f, names in defined_names.items():
        registered = registered_names.get(f, frozenset())
        public_count = sum(1 for n in names if _is_public(n) and n not in registered)
        if public_count > _MAX_PUBLIC_NAMES:
            out.append(_FileTooWide(f, public_count))
    out.sort(key=lambda v: str(v.file))
    return tuple(out)


def find_structure_findings(
    repo: Path,
    py_files: Sequence[Path],
    dirs_with_py: AbstractSet[Path],
    defined_names: Mapping[Path, frozenset[str]],
    registered_names: Mapping[Path, frozenset[str]],
    excludes: Sequence[str] = (),
) -> _StructureFindings:
    return _StructureFindings(
        too_long=_find_file_size_violations(repo, py_files),
        too_many_entries=_find_dir_entry_violations(dirs_with_py, py_files, repo),
        package_too_big=_find_package_size_violations(repo, py_files, excludes),
        too_wide=_find_file_width_violations(defined_names, registered_names),
    )


def print_structure_findings(findings: _StructureFindings) -> bool:
    total = (
        len(findings.too_long)
        + len(findings.too_many_entries)
        + len(findings.package_too_big)
        + len(findings.too_wide)
    )
    print(f"## Structure limits: {total} finding(s)")
    for v in findings.too_long:
        print(f"- {v.file}: {v.lines} lines (limit {_MAX_FILE_LINES})")
    for v in findings.too_many_entries:
        label = "repo root" if v.directory == Path(".") else str(v.directory)
        print(f"- {label}: {v.entries} entries (limit {_MAX_DIR_ENTRIES})")
    for v in findings.package_too_big:
        label = "repo root" if v.package == Path(".") else str(v.package)
        print(f"- {label}: {v.files} files (limit {_MAX_PACKAGE_FILES})")
    for v in findings.too_wide:
        print(f"- {v.file}: {v.names} public names (limit {_MAX_PUBLIC_NAMES})")
    print()
    return total > 0
