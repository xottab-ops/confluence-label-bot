"""
From table rows to a rollout plan: waves, edges, entry points and what is stuck.

The order is Kahn's: rows with no outstanding dependencies form a wave, the wave
is removed and the next one is taken. Along the way we collect errors (cycles,
duplicate releases) and warnings — reporting prints them.
"""
from __future__ import annotations

from collections import defaultdict

from .model import CANCELLED, DONE, FINISHED, Plan, Row

CYCLE_PREFIX = "Cyclic dependency"


def build_plan(rows: list[Row]) -> Plan:
    errors: list[str] = []
    warnings: list[str] = []

    owner = release_owners(rows, errors, warnings)
    edge_keys, external = collect_edges(rows, owner, errors)
    for num, keys in external.items():
        warnings.append(
            f"Row {num}: dependencies outside this rollout {', '.join(keys)} "
            f"— check that they are already installed"
        )

    waves = sort_waves(rows, edge_keys, errors)
    edges = [(s, d, k) for (s, d), k in edge_keys.items()]
    entry, ready, blocked = start_points(rows, edge_keys, errors)

    # Prerequisites are a separate kind of edge: task -> row
    prereq_edges = []
    for r in rows:
        for key in r.prereqs:
            if key in owner:
                warnings.append(
                    f"Row {r.num}: {key} is a release of this rollout listed under "
                    f"«before rollout» — move it to the release dependencies column"
                )
            prereq_edges.append((key, r.num))

    return Plan(waves, edges, dict(external), errors, warnings,
                entry, ready, blocked, prereq_edges)


def release_owners(rows: list[Row], errors: list[str], warnings: list[str]) -> dict[str, str]:
    """Which row installs which release."""
    owner: dict[str, str] = {}
    for r in rows:
        if not r.releases:
            errors.append(f"Row {r.num}: no release given")
        for key in r.releases:
            if key in owner and owner[key] != r.num:
                errors.append(f"Release {key} is listed in rows {owner[key]} and {r.num}")
            owner[key] = r.num
        for col in r.unfilled:
            warnings.append(f"Row {r.num}: column «{col}» is left empty")
    return owner


def collect_edges(rows: list[Row], owner: dict[str, str], errors: list[str]):
    """Edges inside this rollout, plus dependencies that live outside it."""
    edge_keys: dict[tuple[str, str], list[str]] = defaultdict(list)
    external: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        for key in r.depends_on:
            src = owner.get(key)
            if src is None:
                external[r.num].append(key)
            elif src == r.num:
                errors.append(f"Row {r.num}: depends on its own release {key}")
            else:
                edge_keys[(src, r.num)].append(key)
    return edge_keys, external


def sort_waves(rows: list[Row], edge_keys, errors: list[str]) -> list[list[str]]:
    """Topological sort into waves (Kahn)."""
    nums = [r.num for r in rows]
    indeg = {n: 0 for n in nums}
    children = defaultdict(list)
    for (src, dst) in edge_keys:
        indeg[dst] += 1
        children[src].append(dst)

    waves: list[list[str]] = []
    current = [n for n in nums if indeg[n] == 0]
    seen = 0
    while current:
        waves.append(current)
        seen += len(current)
        nxt = []
        for n in current:
            for c in children[n]:
                indeg[c] -= 1
                if indeg[c] == 0:
                    nxt.append(c)
        current = sorted(nxt, key=nums.index)

    if seen < len(nums):
        stuck = [n for n in nums if indeg[n] > 0]
        cycle = find_cycle(stuck, children)
        errors.append(
            f"{CYCLE_PREFIX}: " + " → ".join(cycle) if cycle
            else f"Could not order the rows: {', '.join(stuck)}"
        )
    return waves


def start_points(rows: list[Row], edge_keys, errors: list[str]):
    """
    entry   — structural entry points: rows with no dependency inside this rollout.
    ready   — what can be started now, statuses taken into account: the row itself
              is not finished and every parent of it is done.
    blocked — rows with a cancelled ancestor (transitively).
    If the graph has a cycle, ready is not computed: nothing can start until it
    is fixed.
    """
    code = {r.num: r.code for r in rows}
    parents = defaultdict(list)
    children = defaultdict(list)
    for (src, dst) in edge_keys:
        parents[dst].append(src)
        children[src].append(dst)

    entry = [r.num for r in rows if not parents[r.num]]

    blocked: set[str] = set()
    stack = [n for n, c in code.items() if c == CANCELLED]
    while stack:
        for child in children[stack.pop()]:
            if child not in blocked and code[child] not in FINISHED:
                blocked.add(child)
                stack.append(child)

    ready = []
    if not any(e.startswith(CYCLE_PREFIX) for e in errors):
        for r in rows:
            if r.code in FINISHED or r.num in blocked:
                continue
            if all(code[p] == DONE for p in parents[r.num]):
                ready.append(r.num)

    order = [r.num for r in rows]
    return entry, ready, sorted(blocked, key=order.index)


def find_cycle(nodes: list[str], children: dict[str, list[str]]) -> list[str]:
    allowed, state, stack = set(nodes), {}, []

    def dfs(n):
        state[n] = 1
        stack.append(n)
        for c in children[n]:
            if c not in allowed:
                continue
            if state.get(c) == 1:
                return stack[stack.index(c):] + [c]
            if c not in state and (res := dfs(c)):
                return res
        state[n] = 2
        stack.pop()
        return None

    for n in nodes:
        if n not in state and (res := dfs(n)):
            return res
    return []
