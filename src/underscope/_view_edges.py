"""Directory "view" edges: aggregating the file-level import graph into
one directory's view (its inside children plus the outside nodes they
touch), and the inside/outside node split every view-level consumer
shares."""

from pathlib import Path

from underscope._resolve import EdgeKey, EdgeNames

# (view node src, view node tgt)
PairKey = tuple[Path, Path]
PairEdges = dict[PairKey, set[EdgeKey]]


def lca(a: Path, b: Path) -> Path:
    pa = () if a == Path(".") else a.parts
    pb = () if b == Path(".") else b.parts
    common: list[str] = []
    for x, y in zip(pa, pb):
        if x != y:
            break
        common.append(x)
    return Path(*common) if common else Path(".")


def child_of(directory: Path, path: Path) -> Path | None:
    if path == directory:
        return None
    if directory == Path("."):
        return Path(path.parts[0])
    rel = path.relative_to(directory)
    return directory / rel.parts[0]


def outside_node(directory: Path, path: Path) -> Path | None:
    return child_of(lca(directory, path), path)


def build_view(directory: Path, edge_names: EdgeNames) -> PairEdges:
    # (node_src, node_tgt) -> set of (importer_rel, target_rel) file-level edges
    pair_edges: PairEdges = {}
    for f_rel, t_rel in edge_names:
        f_in = f_rel.is_relative_to(directory)
        t_in = t_rel.is_relative_to(directory)
        if not f_in and not t_in:
            continue
        if f_in and t_in:
            src = child_of(directory, f_rel)
            tgt = child_of(directory, t_rel)
        elif f_in:
            src = child_of(directory, f_rel)
            tgt = outside_node(directory, t_rel)
        else:
            src = outside_node(directory, f_rel)
            tgt = child_of(directory, t_rel)
        if src is None or tgt is None or src == tgt:
            continue
        pair_edges.setdefault((src, tgt), set()).add((f_rel, t_rel))
    return pair_edges


def compute_inside_nodes(directory: Path, pair_edges: PairEdges) -> set[Path]:
    nodes: set[Path] = set()
    for src, tgt in pair_edges:
        nodes.add(src)
        nodes.add(tgt)
    outside = {n for n in nodes if not n.is_relative_to(directory)}
    return nodes - outside
