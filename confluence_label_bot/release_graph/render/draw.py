"""What goes onto the canvas and how: nodes, arrows, legend."""
from __future__ import annotations

import math

from .canvas import bezier_points
from .layout import Diagram, Node, route, to_curves
from .text import Metrics
from .theme import (ARROW_HALF, ARROW_LEN, BOX_STYLES, EDGE_STYLES, ENTRY_STROKE_WIDTH,
                    HEX_SLANT, LEGEND_BOX, LEGEND_GAP, LEGEND_ITEMS, LEGEND_SIZE,
                    LEGEND_STEP, LEGEND_TITLE, LINE_GAP, PAD_X, START_TEXT, SUB_COLOR,
                    SUB_SIZE, TEXT_COLOR, TITLE_SIZE, TITLE_TO_SUB)


def draw_diagram(canvas, diagram: Diagram) -> None:
    for edge in diagram.edges:
        color, thickness, dash = EDGE_STYLES[edge.kind]
        curves = to_curves(route(edge, diagram.nodes))
        canvas.curve(curves, stroke=color, width=thickness, dash=dash)
        draw_arrow(canvas, bezier_points(curves[-1]), color)
    for node in diagram.real_nodes:
        draw_node(canvas, node)


def draw_arrow(canvas, points: list[tuple[float, float]], color: str) -> None:
    (x0, y0), (x1, y1) = points[-2], points[-1]
    angle = math.atan2(y1 - y0, x1 - x0)
    tip = (x1 + ARROW_LEN * math.cos(angle), y1 + ARROW_LEN * math.sin(angle))
    left = (x1 + ARROW_HALF * math.sin(angle), y1 - ARROW_HALF * math.cos(angle))
    right = (x1 - ARROW_HALF * math.sin(angle), y1 + ARROW_HALF * math.cos(angle))
    canvas.polygon([tip, left, right], fill=color, stroke=color, width=1.0)


def draw_node(canvas, node: Node) -> None:
    draw_shape(canvas, node)
    color = START_TEXT if node.style == "start" else TEXT_COLOR
    if node.style == "start":   # we draw the play mark ourselves: the font may not have that glyph
        left = node.x + PAD_X + 2
        canvas.polygon([(left, node.cy - 6), (left + 10, node.cy), (left, node.cy + 6)],
                       fill=color, stroke=color, width=1.0)
    draw_label(canvas, node, color)


def draw_shape(canvas, node: Node) -> None:
    fill, stroke, width = BOX_STYLES.get(node.style, BOX_STYLES["plain"])
    if node.entry:
        width = ENTRY_STROKE_WIDTH
    if node.kind == "hex":
        x, y, w, h = node.x, node.y, node.w, node.h
        canvas.polygon([(x + HEX_SLANT, y), (x + w - HEX_SLANT, y), (x + w, y + h / 2),
                        (x + w - HEX_SLANT, y + h), (x + HEX_SLANT, y + h), (x, y + h / 2)],
                       fill=fill, stroke=stroke, width=width)
    else:
        radius = node.h / 2 if node.kind == "capsule" else 6.0
        canvas.rect(node.x, node.y, node.w, node.h,
                    fill=fill, stroke=stroke, width=width, radius=radius)


def draw_label(canvas, node: Node, color: str) -> None:
    block = len(node.title) * TITLE_SIZE * LINE_GAP
    if node.subtitle:
        block += TITLE_TO_SUB + len(node.subtitle) * SUB_SIZE * LINE_GAP
    y = node.cy - block / 2
    x = node.cx + (12 if node.style == "start" else 0)

    for line in node.title:
        canvas.text(x, y + TITLE_SIZE * LINE_GAP / 2, line,
                    size=TITLE_SIZE, fill=color, bold=True)
        y += TITLE_SIZE * LINE_GAP
    y += TITLE_TO_SUB if node.subtitle else 0
    sub_color = color if node.style == "start" else SUB_COLOR
    for line in node.subtitle:
        canvas.text(x, y + SUB_SIZE * LINE_GAP / 2, line, size=SUB_SIZE, fill=sub_color)
        y += SUB_SIZE * LINE_GAP


def legend_items(diagram: Diagram) -> list[tuple[str, str]]:
    """Only the states the graph actually shows make it into the legend."""
    used = {n.style for n in diagram.real_nodes}
    return [(style, caption) for style, caption in LEGEND_ITEMS if style in used]


def legend_width(metrics: Metrics, items) -> float:
    box_w, _box_h = LEGEND_BOX
    total = metrics.width(LEGEND_TITLE, LEGEND_SIZE, True) + 18
    for _style, caption in items:
        total += box_w + LEGEND_GAP + metrics.width(caption, LEGEND_SIZE) + LEGEND_STEP
    return total


def draw_legend(canvas, x: float, y: float, metrics: Metrics, items) -> None:
    box_w, box_h = LEGEND_BOX
    title_w = metrics.width(LEGEND_TITLE, LEGEND_SIZE, True)
    canvas.text(x + title_w / 2, y, LEGEND_TITLE, size=LEGEND_SIZE, fill=SUB_COLOR, bold=True)
    cursor = x + title_w + 18
    for style, caption in items:
        fill, stroke, width = BOX_STYLES[style]
        canvas.rect(cursor, y - box_h / 2, box_w, box_h, fill=fill, stroke=stroke,
                    width=min(width, 1.6), radius=3.0)
        text_w = metrics.width(caption, LEGEND_SIZE)
        canvas.text(cursor + box_w + LEGEND_GAP + text_w / 2, y, caption,
                    size=LEGEND_SIZE, fill=SUB_COLOR)
        cursor += box_w + LEGEND_GAP + text_w + LEGEND_STEP
