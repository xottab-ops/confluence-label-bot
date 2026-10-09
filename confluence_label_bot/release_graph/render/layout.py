"""
Laying the graph out: nodes, layers, order inside a layer and coordinates.

The layers are not computed here — they already exist in plan.waves after the
topological sort. What is left is textbook Sugiyama: order the nodes inside a
layer by barycentres, lead long edges through dummy nodes and spread everything
out vertically.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..model import Plan, Row
from .text import Metrics, wrap
from .theme import (ARROW_LEN, CURVE_GRIP, DUMMY_HEIGHT, EXTERNAL_CAPTION, HEX_SLANT,
                    LAYER_GAP, LINE_GAP, MARGIN, NODE_GAP, NODE_MIN_W, PAD_X, PAD_Y,
                    PREREQ_CAPTION, START_EXTRA, START_LABEL, SUB_SIZE, TITLE_SIZE,
                    TITLE_TO_SUB)

START = "__start__"
EXTERNAL_PREFIX = "ext:"
PREREQ_PREFIX = "pre:"


@dataclass
class Node:
    id: str
    kind: str = "box"                # box | capsule | hex | dummy
    style: str = "plain"
    title: list[str] = field(default_factory=list)
    subtitle: list[str] = field(default_factory=list)
    entry: bool = False
    layer: int = 0
    x: float = 0.0
    y: float = 0.0
    w: float = 0.0
    h: float = 0.0

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


@dataclass
class Edge:
    src: str
    dst: str
    kind: str = "normal"
    via: list[str] = field(default_factory=list)   # dummy nodes along the way


@dataclass
class Diagram:
    nodes: dict[str, Node]
    edges: list[Edge]
    width: float
    height: float

    @property
    def real_nodes(self) -> list[Node]:
        return [n for n in self.nodes.values() if n.kind != "dummy"]


def build_graph(rows: list[Row], plan: Plan, state_of) -> tuple[dict[str, Node], list[Edge]]:
    nodes: dict[str, Node] = {START: Node(START, kind="capsule", style="start",
                                          title=[START_LABEL])}
    edges: list[Edge] = []

    for r in rows:
        nodes[r.num] = Node(r.num, style=state_of(r, plan), entry=r.num in plan.entry_points,
                            title=[r.label], subtitle=[", ".join(r.releases) or "—"])
    for num in plan.entry_points:
        edges.append(Edge(START, num, "start"))
    for src, dst, _keys in plan.edges:
        edges.append(Edge(src, dst))

    for num, keys in plan.external.items():
        for key in keys:
            ext = EXTERNAL_PREFIX + key
            nodes[ext] = Node(ext, style="external", title=[key], subtitle=[EXTERNAL_CAPTION])
            edges.append(Edge(ext, num, "external"))
    for key, num in plan.prereq_edges:
        pre = PREREQ_PREFIX + key
        if pre not in nodes:
            nodes[pre] = Node(pre, kind="hex", style="prereq",
                              title=[key], subtitle=[PREREQ_CAPTION])
        edges.append(Edge(pre, num, "prereq"))
    return nodes, edges


def assign_layers(nodes: dict[str, Node], edges: list[Edge], plan: Plan) -> None:
    """Rows follow the waves, helper nodes sit in the layer before their consumer."""
    for i, wave in enumerate(plan.waves, start=1):
        for num in wave:
            if num in nodes:
                nodes[num].layer = i
    nodes[START].layer = 0

    # rows caught in a cycle never made it into a wave — give them a column of
    # their own on the right instead of piling them onto "Start"
    placed = {num for wave in plan.waves for num in wave}
    tail = len(plan.waves) + 1
    for node in nodes.values():
        if node.id not in placed and not node.id.startswith((EXTERNAL_PREFIX, PREREQ_PREFIX, "__")):
            node.layer = tail

    for node in nodes.values():
        if node.id.startswith((EXTERNAL_PREFIX, PREREQ_PREFIX)):
            consumers = [nodes[e.dst].layer for e in edges if e.src == node.id and e.dst in nodes]
            node.layer = max(0, min(consumers, default=1) - 1)


def insert_dummies(nodes: dict[str, Node], edges: list[Edge]) -> None:
    """An edge crossing several layers goes through a chain of dummies, clear of the boxes."""
    for n, edge in enumerate(edges):
        src, dst = nodes[edge.src], nodes[edge.dst]
        for layer in range(src.layer + 1, dst.layer):
            key = f"dummy:{n}:{layer}"
            # the height is not zero: it makes the spreading pass keep a transit
            # edge at a respectful distance from the boxes it flies over
            nodes[key] = Node(key, kind="dummy", layer=layer, w=1.0, h=DUMMY_HEIGHT)
            edge.via.append(key)


def neighbours(nodes: dict[str, Node], edges: list[Edge]) -> tuple[dict, dict]:
    """Who sits to the left and to the right of a node, dummies included."""
    prev_of: dict[str, list[str]] = {n: [] for n in nodes}
    next_of: dict[str, list[str]] = {n: [] for n in nodes}
    for edge in edges:
        seq = [edge.src, *edge.via, edge.dst]
        for a, b in zip(seq, seq[1:]):
            next_of[a].append(b)
            prev_of[b].append(a)
    return prev_of, next_of


def order_layers(nodes: dict[str, Node], edges: list[Edge], passes: int = 6) -> list[list[Node]]:
    """Barycentres: a node drifts towards the average position of its neighbours."""
    prev_of, next_of = neighbours(nodes, edges)
    depth = max(n.layer for n in nodes.values()) + 1
    layers = [[n for n in nodes.values() if n.layer == i] for i in range(depth)]
    pos = {n.id: float(i) for layer in layers for i, n in enumerate(layer)}

    for step in range(passes):
        forward = step % 2 == 0
        side = prev_of if forward else next_of
        for i in (range(1, depth) if forward else range(depth - 2, -1, -1)):
            for node in layers[i]:
                near = [pos[m] for m in side[node.id] if m in pos]
                if near:
                    pos[node.id] = sum(near) / len(near)
            layers[i].sort(key=lambda n: pos[n.id])
            for j, node in enumerate(layers[i]):
                pos[node.id] = float(j)
    return layers


def measure(nodes: dict[str, Node], metrics: Metrics, max_w: float) -> None:
    for node in nodes.values():
        if node.kind == "dummy":
            continue
        node.title = [ln for t in node.title for ln in wrap(t, metrics, TITLE_SIZE, True, max_w)]
        node.subtitle = [ln for t in node.subtitle
                         for ln in wrap(t, metrics, SUB_SIZE, False, max_w)]
        widest = max([metrics.width(ln, TITLE_SIZE, True) for ln in node.title] +
                     [metrics.width(ln, SUB_SIZE) for ln in node.subtitle] + [0.0])
        extra = 2 * HEX_SLANT if node.kind == "hex" else 0.0
        if node.style == "start":
            extra += START_EXTRA
        node.w = max(NODE_MIN_W, widest + 2 * PAD_X) + extra
        node.h = text_block_height(node) + 2 * PAD_Y


def text_block_height(node: Node) -> float:
    height = len(node.title) * TITLE_SIZE * LINE_GAP
    if node.subtitle:
        height += TITLE_TO_SUB + len(node.subtitle) * SUB_SIZE * LINE_GAP
    return height


def place(layers: list[list[Node]], nodes: dict[str, Node], edges: list[Edge],
          passes: int = 8) -> None:
    """Layers become columns left to right; inside a column nodes pull towards neighbours."""
    x = MARGIN
    for layer in layers:
        width = max((n.w for n in layer), default=0.0)
        for node in layer:
            node.x = x + (width - node.w) / 2
        x += width + LAYER_GAP

    for layer in layers:                       # initial top-down spread
        y = MARGIN
        for node in layer:
            node.y = y
            y += node.h + NODE_GAP

    prev_of, next_of = neighbours(nodes, edges)
    for step in range(passes):
        for layer in (layers if step % 2 == 0 else layers[::-1]):
            for node in layer:
                near = [nodes[m].cy for m in prev_of[node.id] + next_of[node.id]]
                if near:
                    node.y = sum(near) / len(near) - node.h / 2
            pack(layer)
    align(layers)


def pack(layer: list[Node]) -> None:
    """Undo the overlaps, keeping the order."""
    layer.sort(key=lambda n: n.y)
    for prev, node in zip(layer, layer[1:]):
        node.y = max(node.y, prev.y + prev.h + NODE_GAP)


def align(layers: list[list[Node]]) -> None:
    """Centre the layers against each other and pull the drawing up to the margin."""
    spans = [(min(n.y for n in l), max(n.y + n.h for n in l)) for l in layers if l]
    if not spans:
        return
    middle = sum((a + b) / 2 for a, b in spans) / len(spans)
    for layer in layers:
        if not layer:
            continue
        low, high = min(n.y for n in layer), max(n.y + n.h for n in layer)
        shift = middle - (low + high) / 2
        for node in layer:
            node.y += shift
    top = min(n.y for layer in layers for n in layer)
    for layer in layers:
        for node in layer:
            node.y += MARGIN - top


def route(edge: Edge, nodes: dict[str, Node]) -> list[tuple[float, float]]:
    """The points an edge runs through: source's right side, dummies, target's left side."""
    src, dst = nodes[edge.src], nodes[edge.dst]
    points = [(src.x + src.w, src.cy)]
    points += [(nodes[d].cx, nodes[d].cy) for d in edge.via]
    points.append((dst.x - ARROW_LEN, dst.cy))
    return points


def to_curves(points: list[tuple[float, float]]) -> list[tuple]:
    """Polyline -> cubic Beziers with horizontal tangents."""
    curves = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        grip = max(18.0, abs(x1 - x0) * CURVE_GRIP)
        curves.append(((x0, y0), (x0 + grip, y0), (x1 - grip, y1), (x1, y1)))
    return curves


def layout(rows: list[Row], plan: Plan, state_of, metrics: Metrics,
           max_node_width: float) -> Diagram:
    nodes, edges = build_graph(rows, plan, state_of)
    assign_layers(nodes, edges, plan)
    insert_dummies(nodes, edges)
    measure(nodes, metrics, max_node_width)
    layers = order_layers(nodes, edges)
    place(layers, nodes, edges)

    real = [n for n in nodes.values() if n.kind != "dummy"]
    width = max(n.x + n.w for n in real) + MARGIN
    height = max(n.y + n.h for n in real) + MARGIN
    return Diagram(nodes, edges, width, height)
