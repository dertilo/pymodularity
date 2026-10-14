"""The privacy rules `underscope check` enforces (the cycle ban itself
lives in `_cycles`), and the printing of all check findings."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from underscope._cycles import CycleFinding
from underscope._resolve import EdgeNames, is_private_name, module_comp, should_skip_dir


def has_project_table(pyproject: Path) -> bool:
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    return "project" in data


def find_package_roots(repo: Path) -> list[Path]:
    """Directories (relative to repo) whose pyproject.toml has a [project]
    table, used only to anchor the privacy rule's "package root". Sorted
    deepest-first so the closest ancestor is found first."""
    roots: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        if "pyproject.toml" in filenames and has_project_table(
            Path(dirpath) / "pyproject.toml"
        ):
            roots.append(Path(dirpath).relative_to(repo))
    return sorted(roots, key=lambda p: len(p.parts), reverse=True)


def owning_package_root(rel: Path, package_roots: list[Path]) -> Path:
    for root in package_roots:
        if root != Path(".") and rel.is_relative_to(root):
            return root
    return Path(".")


def private_owner_dir(rel: Path, package_roots: list[Path]) -> Path | None:
    """The directory a private file/dir component makes it private to, or
    None if no component of rel (relative to its package root) is private."""
    root = owning_package_root(rel, package_roots)
    sub = rel.relative_to(root) if root != Path(".") else rel
    comp = module_comp(sub.parts)
    for k, part in enumerate(comp):
        if is_private_name(part):
            return root / Path(*comp[:k]) if comp[:k] else root
    return None


@dataclass(frozen=True, slots=True)
class PrivacyViolation:
    """An import that reaches a module private to a directory it is not
    under."""

    importer: Path
    target: Path
    owner: Path


def find_privacy_violations(
    edge_names: EdgeNames, package_roots: list[Path]
) -> list[PrivacyViolation]:
    violations: list[PrivacyViolation] = []
    owner_cache: dict[Path, Path | None] = {}

    def owner_of(rel: Path) -> Path | None:
        if rel not in owner_cache:
            owner_cache[rel] = private_owner_dir(rel, package_roots)
        return owner_cache[rel]

    for importer_rel, target_rel in edge_names:
        owner = owner_of(target_rel)
        if owner is None or importer_rel.is_relative_to(owner):
            continue
        violations.append(PrivacyViolation(importer_rel, target_rel, owner))

    violations.sort(key=lambda v: (str(v.importer), str(v.target)))
    return violations


def name_owner(target_rel: Path) -> tuple[Path, bool]:
    """Where a name imported `from target_rel import _x` is private to:
    (owner, owner_is_dir). A package (`__init__.py` file, or a namespace
    package directory with no `__init__.py`) makes its names visible to
    files under that directory; a single module file makes its names
    visible to no other file at all."""
    if target_rel.name == "__init__.py":
        return target_rel.parent, True
    if target_rel.suffix != ".py":
        return target_rel, True
    return target_rel, False


@dataclass(frozen=True, slots=True)
class NameViolation:
    """An import of a private name (`from m import _x`) that reaches
    outside the directory (package) or file that name is private to."""

    importer: Path
    target: Path
    name: str
    owner: Path
    owner_is_dir: bool


def find_name_violations(edge_names: EdgeNames) -> list[NameViolation]:
    violations: list[NameViolation] = []
    for (importer_rel, target_rel), names in edge_names.items():
        for name in names:
            if not is_private_name(name):
                continue
            owner, owner_is_dir = name_owner(target_rel)
            if owner_is_dir and importer_rel.is_relative_to(owner):
                continue
            violations.append(
                NameViolation(importer_rel, target_rel, name, owner, owner_is_dir)
            )
    violations.sort(key=lambda v: (str(v.importer), str(v.target), v.name))
    return violations


def print_check_findings(
    cycle_findings: list[CycleFinding],
    distinct_file_cycles: int,
    privacy_violations: list[PrivacyViolation],
    name_violations: list[NameViolation],
) -> bool:
    """Prints findings, returns True if any finding exists."""
    any_finding = (
        bool(cycle_findings) or bool(privacy_violations) or bool(name_violations)
    )

    print(
        f"## Cycles: {len(cycle_findings)} finding(s) across all directory "
        + f"views, from {distinct_file_cycles} distinct file-level cycle(s)"
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
        for a, b in cf.example_files:
            print(f"    {a} -> {b}")
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
