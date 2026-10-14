"""Tarjan SCC, on the directory view edges `_view_edges.build_view`
produces (feeding both `check`'s cycle ban and the Cycles section of
`graph`'s views), and the file-level cycle count on the raw file graph."""

import itertools
from dataclasses import dataclass
from pathlib import Path

from underscope._resolve import EdgeKey, EdgeNames
from underscope._view_edges import PairEdges, build_view, compute_inside_nodes


@dataclass(frozen=True, slots=True)
class _MutualPair:
    """Two SCC members with edges in both directions, and each direction's
    distinct (importer file, target file) pair count."""

    a: Path
    b: Path
    a_to_b_files: int
    b_to_a_files: int


@dataclass(frozen=True, slots=True)
class _SccEdge:
    """One directed edge inside a strongly connected component."""

    src: Path
    tgt: Path
    files: int


@dataclass(frozen=True, slots=True)
class _Scc:
    """A strongly connected component (size >= 2) among a view's inside
    children, on the edges the view draws."""

    members: list[Path]
    mutual_pairs: list[_MutualPair]
    edges: list[_SccEdge]


def _tarjan_sccs(nodes: set[Path], adj: dict[Path, set[Path]]) -> list[list[Path]]:
    """Iterative Tarjan (explicit stack, no recursion) so large views can't
    hit the recursion limit."""
    counter = 0
    index: dict[Path, int] = {}
    lowlink: dict[Path, int] = {}
    on_stack: dict[Path, bool] = {}
    stack: list[Path] = []
    sccs: list[list[Path]] = []

    for start in sorted(nodes, key=str):
        if start in index:
            continue
        work: list[tuple[Path, list[Path], int]] = [
            (start, sorted(adj.get(start, ()), key=str), 0)
        ]
        index[start] = lowlink[start] = counter
        counter += 1
        stack.append(start)
        on_stack[start] = True

        while work:
            v, children, pos = work[-1]
            advanced = False
            while pos < len(children):
                w = children[pos]
                pos += 1
                if w not in index:
                    index[w] = lowlink[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack[w] = True
                    work[-1] = (v, children, pos)
                    work.append((w, sorted(adj.get(w, ()), key=str), 0))
                    advanced = True
                    break
                if on_stack.get(w, False):
                    lowlink[v] = min(lowlink[v], index[w])
            if advanced:
                continue
            work[-1] = (v, children, pos)
            work.pop()
            if work:
                parent = work[-1][0]
                lowlink[parent] = min(lowlink[parent], lowlink[v])
            if lowlink[v] == index[v]:
                comp: list[Path] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    comp.append(w)
                    if w == v:
                        break
                sccs.append(comp)
    return sccs


def compute_sccs(inside_nodes: set[Path], pair_edges: PairEdges) -> list[_Scc]:
    adj: dict[Path, set[Path]] = {n: set() for n in inside_nodes}
    file_counts: dict[EdgeKey, int] = {}
    for (src, tgt), file_pairs in pair_edges.items():
        if src in inside_nodes and tgt in inside_nodes:
            adj[src].add(tgt)
            file_counts[(src, tgt)] = len(file_pairs)

    raw = [comp for comp in _tarjan_sccs(inside_nodes, adj) if len(comp) >= 2]

    result: list[_Scc] = []
    for comp in raw:
        members = sorted(comp, key=str)
        member_set = set(comp)
        mutual_pairs: list[_MutualPair] = []
        seen: set[frozenset[Path]] = set()
        for a in members:
            for b in members:
                if a == b:
                    continue
                if (a, b) in file_counts and (b, a) in file_counts:
                    key = frozenset((a, b))
                    if key in seen:
                        continue
                    seen.add(key)
                    mutual_pairs.append(
                        _MutualPair(a, b, file_counts[(a, b)], file_counts[(b, a)])
                    )
        mutual_pairs.sort(key=lambda mp: (str(mp.a), str(mp.b)))
        edges = sorted(
            (
                _SccEdge(s, t, c)
                for (s, t), c in file_counts.items()
                if s in member_set and t in member_set
            ),
            key=lambda e: (str(e.src), str(e.tgt)),
        )
        result.append(_Scc(members, mutual_pairs, edges))

    result.sort(key=lambda scc: (-len(scc.members), str(scc.members[0])))
    return result


def _find_example_cycle(members: list[Path], pair_edges: PairEdges) -> list[Path]:
    """One concrete node loop (first == last) through the SCC, via DFS."""
    member_set = set(members)
    adj: dict[Path, list[Path]] = {m: [] for m in members}
    for src, tgt in pair_edges:
        if src in member_set and tgt in member_set and src != tgt:
            adj[src].append(tgt)
    for neighbors in adj.values():
        neighbors.sort(key=str)

    start = members[0]
    stack: list[Path] = [start]
    visited: set[Path] = {start}

    def dfs(node: Path) -> list[Path] | None:
        for nxt in adj[node]:
            if nxt == start and len(stack) > 1:
                return [*stack, start]
            if nxt not in visited:
                visited.add(nxt)
                stack.append(nxt)
                result = dfs(nxt)
                if result is not None:
                    return result
                stack.pop()
        return None

    return dfs(start) or [start]


def _example_file_edges(
    example: list[Path], pair_edges: PairEdges
) -> list[tuple[Path, Path]]:
    out: list[tuple[Path, Path]] = []
    for a, b in itertools.pairwise(example):
        file_pairs = pair_edges.get((a, b))
        if file_pairs:
            out.append(min(file_pairs, key=lambda p: (str(p[0]), str(p[1]))))
    return out


@dataclass(frozen=True, slots=True)
class CycleFinding:
    """One SCC (size >= 2) found in one directory's view, with a concrete
    example loop through file-level edges."""

    directory: Path
    members: list[Path]
    example: list[Path]
    example_files: list[tuple[Path, Path]]


def _find_cycle_findings(
    directory: Path, inside_nodes: set[Path], pair_edges: PairEdges
) -> list[CycleFinding]:
    findings: list[CycleFinding] = []
    for scc in compute_sccs(inside_nodes, pair_edges):
        example = _find_example_cycle(scc.members, pair_edges)
        findings.append(
            CycleFinding(
                directory, scc.members, example, _example_file_edges(example, pair_edges)
            )
        )
    return findings


def count_file_level_cycles(adj: dict[Path, set[Path]]) -> int:
    """Distinct cycles on the raw file-level graph, ignoring any directory
    view's aggregation -- the number of genuinely separate root causes."""
    sccs = _tarjan_sccs(set(adj), adj)
    return sum(1 for comp in sccs if len(comp) >= 2)


def find_all_cycle_findings(
    dirs_with_py: set[Path], edge_names: EdgeNames
) -> list[CycleFinding]:
    """The cycle ban `check` enforces: every directory's view, scanned for
    SCCs (size >= 2) among its inside children."""
    findings: list[CycleFinding] = []
    for directory in sorted(dirs_with_py, key=str):
        pair_edges = build_view(directory, edge_names)
        inside_nodes = compute_inside_nodes(directory, pair_edges)
        findings.extend(_find_cycle_findings(directory, inside_nodes, pair_edges))
    return findings
