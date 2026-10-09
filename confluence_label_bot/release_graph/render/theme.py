"""Sizes, colours and styles — every bit of the graph's looks lives here."""
from __future__ import annotations

TITLE_SIZE = 14.0
SUB_SIZE = 11.5
LEGEND_SIZE = 11.5
PAD_X, PAD_Y = 14.0, 11.0
LINE_GAP = 1.32          # line height
TITLE_TO_SUB = 5.0
NODE_MIN_W = 130.0
LAYER_GAP = 96.0         # between layer columns
NODE_GAP = 26.0          # between nodes inside a layer
DUMMY_HEIGHT = 18.0      # "thickness" of a transit edge, so it keeps off the boxes
CURVE_GRIP = 0.35        # how sharply a Bezier bends
MARGIN = 28.0
HEX_SLANT = 16.0         # the slant of a prerequisite hexagon
START_EXTRA = 30.0       # room for the triangle inside "Start"
ARROW_LEN, ARROW_HALF = 10.0, 5.0

TEXT_COLOR = "#1f2933"
SUB_COLOR = "#52606d"
START_TEXT = "#ffffff"

START_LABEL = "Start"

# row state -> fill, stroke, stroke width
BOX_STYLES = {
    "plain": ("#ffffff", "#8b96a5", 1.6),
    "done": ("#d3f5d3", "#2e7d32", 1.6),
    "cancelled": ("#f8d7da", "#c62828", 1.6),
    "ready": ("#d6e9ff", "#1565c0", 3.0),
    "blocked": ("#ffe0b2", "#ef6c00", 1.6),
    "external": ("#eeeeee", "#999999", 1.6),
    "prereq": ("#fff8e1", "#f9a825", 1.6),
    "start": ("#1565c0", "#0d47a1", 1.6),
}
ENTRY_STROKE_WIDTH = 3.0

# edge kind -> colour, width, dash
EDGE_STYLES = {
    "normal": ("#5a6472", 1.8, None),
    "start": ("#0d47a1", 2.6, None),
    "external": ("#999999", 1.6, (6.0, 4.0)),
    "prereq": ("#f9a825", 2.0, (2.0, 4.0)),
}

EXTERNAL_CAPTION = "external release"
PREREQ_CAPTION = "before rollout"

LEGEND_ITEMS = [
    ("done", "completed"),
    ("cancelled", "cancelled"),
    ("ready", "ready to start"),
    ("blocked", "blocked"),
    ("plain", "waiting for dependencies"),
    ("external", EXTERNAL_CAPTION),
    ("prereq", PREREQ_CAPTION),
]
LEGEND_TITLE = "Legend:"
LEGEND_BOX = (22.0, 13.0)      # size of a colour swatch
LEGEND_GAP = 8.0               # between the swatch and its caption
LEGEND_STEP = 22.0             # between legend entries
LEGEND_HEIGHT = 34.0
