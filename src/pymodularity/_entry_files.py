"""Entry files: how much of a directory outside code reaches into
directly. Split out of `_coupling_metrics` to keep that module under
the line-count soft cap."""

from collections.abc import Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path

from pymodularity._import_parse.import_graph import EdgeNames
from pymodularity._reach_scores import ancestor_dirs


@dataclass(frozen=True, slots=True)
class _EntryFiles:
    """A directory's entry-file share: how many of its files at least one
    file outside the directory imports directly, out of all files inside
    it; outside_importers is the distinct outside files doing so."""

    directory: Path
    entry: int
    files: int
    share: float
    outside_importers: int


def compute_entry_files(
    dirs_with_py: AbstractSet[Path], nodes: AbstractSet[Path], edge_names: EdgeNames
) -> Mapping[Path, _EntryFiles]:
    entry_by_dir: dict[Path, set[Path]] = {}
    outside_importers_by_dir: dict[Path, set[Path]] = {}
    for importer_rel, target_rel in edge_names:
        for directory in ancestor_dirs(target_rel):
            if directory not in dirs_with_py:
                continue
            if importer_rel.is_relative_to(directory):
                continue
            entry_by_dir.setdefault(directory, set()).add(target_rel)
            outside_importers_by_dir.setdefault(directory, set()).add(importer_rel)

    files_by_dir: dict[Path, int] = {}
    for f in nodes:
        for directory in ancestor_dirs(f):
            if directory in dirs_with_py:
                files_by_dir[directory] = files_by_dir.get(directory, 0) + 1

    out: dict[Path, _EntryFiles] = {}
    for directory in dirs_with_py:
        files = files_by_dir.get(directory, 0)
        entry = len(entry_by_dir.get(directory, set()))
        share = entry / files if files else 0.0
        outside = len(outside_importers_by_dir.get(directory, set()))
        out[directory] = _EntryFiles(directory, entry, files, share, outside)
    return out
