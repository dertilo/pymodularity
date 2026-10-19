"""The privacy rules `pymodularity check` enforces (the cycle ban itself
lives in `_cycles`), and the printing of all check findings."""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pymodularity._cycles import CycleFinding
from pymodularity._parse.graph import EdgeNames
from pymodularity._parse.names import is_private_name, module_comp


def _owning_package_root(rel: Path, package_roots: Sequence[Path]) -> Path:
    for root in package_roots:
        if root != Path(".") and rel.is_relative_to(root):
            return root
    return Path(".")


def _visibility_scope(rel: Path, package_roots: Sequence[Path]) -> Path:
    """The directory whose subtree may import rel: the private owner of its
    innermost private file/dir component, or the repo root (everyone) if rel
    has no private component (relative to its package root). Several private
    parts each restrict; the narrowest (innermost) one wins."""
    root = _owning_package_root(rel, package_roots)
    sub = rel.relative_to(root) if root != Path(".") else rel
    comp = module_comp(sub.parts)
    for k in range(len(comp) - 1, -1, -1):
        if is_private_name(comp[k]):
            return root / Path(*comp[:k]) if comp[:k] else root
    return Path(".")


@dataclass(frozen=True, slots=True)
class _PrivacyViolation:
    """An import that reaches a module private to a directory it is not
    under."""

    importer: Path
    target: Path
    owner: Path


def find_privacy_violations(
    edge_names: EdgeNames, package_roots: Sequence[Path]
) -> tuple[_PrivacyViolation, ...]:
    violations: list[_PrivacyViolation] = []
    scope_cache: dict[Path, Path] = {}

    def scope_of(rel: Path) -> Path:
        if rel not in scope_cache:
            scope_cache[rel] = _visibility_scope(rel, package_roots)
        return scope_cache[rel]

    for importer_rel, target_rel in edge_names:
        scope = scope_of(target_rel)
        if importer_rel.is_relative_to(scope):
            continue
        violations.append(_PrivacyViolation(importer_rel, target_rel, scope))

    violations.sort(key=lambda v: (str(v.importer), str(v.target)))
    return tuple(violations)


@dataclass(frozen=True, slots=True)
class _NameOwner:
    """Where a name imported `from target_rel import _x` is private to."""

    owner: Path
    owner_is_dir: bool


def _name_owner(target_rel: Path) -> _NameOwner:
    """A package (`__init__.py` file, or a namespace package directory with
    no `__init__.py`) makes its names visible to files under that directory;
    a single module file makes its names visible to no other file at all."""
    if target_rel.name == "__init__.py":
        return _NameOwner(target_rel.parent, True)
    if target_rel.suffix != ".py":
        return _NameOwner(target_rel, True)
    return _NameOwner(target_rel, False)


@dataclass(frozen=True, slots=True)
class _NameViolation:
    """An import of a private name (`from m import _x`) that reaches
    outside the directory (package) or file that name is private to."""

    importer: Path
    target: Path
    name: str
    owner: Path
    owner_is_dir: bool


def find_name_violations(edge_names: EdgeNames) -> tuple[_NameViolation, ...]:
    violations: list[_NameViolation] = []
    for (importer_rel, target_rel), names in edge_names.items():
        for name in names:
            if not is_private_name(name):
                continue
            owner_info = _name_owner(target_rel)
            if owner_info.owner_is_dir and importer_rel.is_relative_to(owner_info.owner):
                continue
            violations.append(
                _NameViolation(
                    importer_rel,
                    target_rel,
                    name,
                    owner_info.owner,
                    owner_info.owner_is_dir,
                )
            )
    violations.sort(key=lambda v: (str(v.importer), str(v.target), v.name))
    return tuple(violations)


def print_check_findings(
    cycle_findings: Sequence[CycleFinding],
    distinct_file_cycles: int,
    privacy_violations: Sequence[_PrivacyViolation],
    name_violations: Sequence[_NameViolation],
) -> bool:
    """Prints findings, returns True if any finding exists."""
    any_finding = (
        bool(cycle_findings) or bool(privacy_violations) or bool(name_violations)
    )

    print(
        f"## Cycles: {len(cycle_findings)} in directory views, "
        + f"{distinct_file_cycles} at file level"
    )
    if cycle_findings:
        print(
            "(a directory view merges each child directory into one node, "
            + "so a view's cycles need not match the file-level cycles: "
            + "2 directories can import each other with no file-level cycle, "
            + "and several file-level cycles can merge into one)"
        )
    for cf in cycle_findings:
        directory_label = "repo root" if cf.directory == Path(".") else str(cf.directory)
        print(f"- {directory_label}: {len(cf.members)} nodes")
        print(f"  members: {', '.join(str(m) for m in cf.members)}")
        print(f"  example: {' -> '.join(str(n) for n in cf.example)}")
        for edge in cf.example_files:
            print(f"    {edge.importer} -> {edge.target}")
    print()

    total_private = len(privacy_violations) + len(name_violations)
    print(f"## Private-item violations: {total_private} finding(s)")
    for v in privacy_violations:
        print(f"- {v.importer} -> {v.target}: allowed only inside {v.owner}")
    for nv in name_violations:
        scope = "directory" if nv.owner_is_dir else "file"
        print(
            f"- {nv.importer} -> {nv.target}: name {nv.name} private to "
            + f"{scope} {nv.owner}"
        )
    print()

    return any_finding
