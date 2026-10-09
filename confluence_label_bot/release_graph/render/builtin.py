"""The built-in renderer: layout -> canvas -> image bytes."""
from __future__ import annotations

from ..model import Plan, Row
from .canvas import CANVASES
from .draw import draw_diagram, draw_legend, legend_items, legend_width
from .layout import layout
from .text import Metrics
from .theme import LEGEND_HEIGHT, MARGIN

DEFAULT_MAX_NODE_WIDTH = 250.0


def render(rows: list[Row], plan: Plan, state_of, *, fmt: str = "png", scale: float = 2.0,
           background: str = "#ffffff", legend: bool = True,
           max_node_width: float = DEFAULT_MAX_NODE_WIDTH) -> bytes:
    if fmt not in CANVASES:
        raise ValueError(f"unknown picture format: {fmt}")

    metrics = Metrics()
    diagram = layout(rows, plan, state_of, metrics, max_node_width)

    items = legend_items(diagram) if legend else []
    legend_y = diagram.height + 4
    if items:
        diagram.width = max(diagram.width, legend_width(metrics, items) + 2 * MARGIN)
        diagram.height += LEGEND_HEIGHT

    canvas = CANVASES[fmt](diagram.width, diagram.height, background,
                           scale=scale, metrics=metrics)
    draw_diagram(canvas, diagram)
    if items:
        draw_legend(canvas, MARGIN, legend_y, metrics, items)
    return canvas.result()
