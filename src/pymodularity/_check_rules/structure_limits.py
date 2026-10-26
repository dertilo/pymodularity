"""The structure-limit rules (file size, directory fan-out, package size,
public top-level-name count per file, and a file/directory name carrying
at least 2 words) `pymodularity check` enforces. All limits are fixed
constants, no config, no baseline. Imported only by `check_command`.

A finding is a symptom, never a target: it names where to look, not what
to do. Moving files until a count passes only shifts the cause, see
docs/rules.md."""

from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from pymodularity._import_parse.package_roots import find_package_roots

_MAX_FILE_LINES = 500
_MAX_DIR_ENTRIES = 10
_MAX_PACKAGE_FILES = 100
_MAX_PUBLIC_NAMES = 10
_MIN_NAME_WORDS = 2

# __init__.py and __main__.py don't count as directory entries (rule 2);
# every other scanned file counts fully for every rule.
_STRUCTURAL_FILES = frozenset({"__init__.py", "__main__.py"})

# Stems exempt from the word-count rule (rule 5): language or tool
# contracts fix their name, not the author -- __init__/__main__ are
# Python package/entry-point hooks, conftest is pytest's, setup/wsgi/asgi
# are the names their respective tools look for, _version is the
# single-source-of-truth file convention most build backends expect.
_EXEMPT_STEMS = frozenset(
    {"__init__", "__main__", "conftest", "setup", "wsgi", "asgi", "_version"}
)

# Directory names exempt from the word-count rule: src is the src-layout
# convention, tests/migrations/alembic hold generated or framework-named
# files the author doesn't choose. A directory whose *parent* is named
# `src` is also exempt (see _find_dir_name_word_violations): the src
# layout is how a repo gets a one-word import package name without being
# the package root itself.
_EXEMPT_DIR_NAMES = frozenset({"src", "tests", "migrations", "alembic"})


def _word_count(stem: str) -> int:
    """The number of non-empty `_`-separated runs in `stem`, after
    stripping leading underscores. `models` is 1, `_common` is 1 (leading
    underscore stripped first), `s3_client` is 2."""
    stripped = stem.lstrip("_")
    return len([word for word in stripped.split("_") if word])


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
class _FileNameTooFewWords:
    file: Path
    words: int


@dataclass(frozen=True, slots=True)
class _DirNameTooFewWords:
    directory: Path
    words: int


@dataclass(frozen=True, slots=True)
class _StructureFindings:
    too_long: tuple[_FileTooLong, ...] = ()
    too_many_entries: tuple[_DirTooManyEntries, ...] = ()
    package_too_big: tuple[_PackageTooManyFiles, ...] = ()
    too_wide: tuple[_FileTooWide, ...] = ()
    file_name_too_few_words: tuple[_FileNameTooFewWords, ...] = ()
    dir_name_too_few_words: tuple[_DirNameTooFewWords, ...] = ()


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


def _find_file_name_word_violations(
    py_files: Sequence[Path], repo: Path
) -> tuple[_FileNameTooFewWords, ...]:
    out: list[_FileNameTooFewWords] = []
    for f in py_files:
        stem = f.stem
        if stem in _EXEMPT_STEMS:
            continue
        words = _word_count(stem)
        if words < _MIN_NAME_WORDS:
            out.append(_FileNameTooFewWords(f.relative_to(repo), words))
    out.sort(key=lambda v: str(v.file))
    return tuple(out)


def _find_dir_name_word_violations(
    dirs_with_py: AbstractSet[Path], package_roots: Sequence[Path]
) -> tuple[_DirNameTooFewWords, ...]:
    out: list[_DirNameTooFewWords] = []
    for d in dirs_with_py:
        if (
            d == Path(".")
            or d in package_roots
            or d.name in _EXEMPT_DIR_NAMES
            or d.parent.name == "src"
        ):
            continue
        words = _word_count(d.name)
        if words < _MIN_NAME_WORDS:
            out.append(_DirNameTooFewWords(d, words))
    out.sort(key=lambda v: str(v.directory))
    return tuple(out)


def find_structure_findings(
    repo: Path,
    py_files: Sequence[Path],
    dirs_with_py: AbstractSet[Path],
    defined_names: Mapping[Path, frozenset[str]],
    registered_names: Mapping[Path, frozenset[str]],
    excludes: Sequence[str] = (),
) -> _StructureFindings:
    package_roots = find_package_roots(repo, excludes)
    return _StructureFindings(
        too_long=_find_file_size_violations(repo, py_files),
        too_many_entries=_find_dir_entry_violations(dirs_with_py, py_files, repo),
        package_too_big=_find_package_size_violations(repo, py_files, excludes),
        too_wide=_find_file_width_violations(defined_names, registered_names),
        file_name_too_few_words=_find_file_name_word_violations(py_files, repo),
        dir_name_too_few_words=_find_dir_name_word_violations(
            dirs_with_py, package_roots
        ),
    )


_SCOPE_GUIDANCE = (
    "  one word admits everything of its kind, so a file or directory grows "
    "with its parent. The fix is a thought, not a second word: what does this "
    "unit hold that must sit together, and what in it does not belong? The "
    "answer may be a split, a move, a merge with a neighbour, or only a name. "
    "A name found that way sets a scope by itself, which concept or which "
    "part. `model_classes.py` or `github_connector.py` pass the rule and "
    "miss its point."
)


_SYMPTOM_GUIDANCE = (
    "  a finding names where to look, not what to do. Moving files until the "
    "count passes only shifts the cause. Read which names cause the imports "
    "there, and remove or invert one dependency at a time. An open finding "
    "is acceptable."
)


def print_structure_findings(findings: _StructureFindings) -> bool:
    total = (
        len(findings.too_long)
        + len(findings.too_many_entries)
        + len(findings.package_too_big)
        + len(findings.too_wide)
        + len(findings.file_name_too_few_words)
        + len(findings.dir_name_too_few_words)
    )
    print(f"## Structure limits: {total} finding(s)")
    if total > 0:
        print(_SYMPTOM_GUIDANCE)
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
    if findings.file_name_too_few_words or findings.dir_name_too_few_words:
        print(_SCOPE_GUIDANCE)
    for v in findings.file_name_too_few_words:
        print(f"- {v.file}: {v.words} word(s) in the name (minimum {_MIN_NAME_WORDS})")
    for v in findings.dir_name_too_few_words:
        print(
            f"- {v.directory}: {v.words} word(s) in the name (minimum {_MIN_NAME_WORDS})"
        )
    print()
    return total > 0
