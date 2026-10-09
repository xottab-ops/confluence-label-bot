"""
Fonts and text measurement.

With Pillow we measure exactly, without it by a table of average widths: boxes
come out a little roomier, but SVG is drawn with no dependencies at all.

The width table covers Cyrillic too — team names come from the page and may
well be in Russian even when the tool itself speaks English.
"""
from __future__ import annotations

import os

FONT_FILES = {
    False: ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf"],
    True: ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
           "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
           "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
}
SVG_FONT = "Segoe UI, Roboto, Helvetica, Arial, sans-serif"

NARROW = set("ijlt.,:;'!|()[]{} ")
WIDE = set("MWМШЩЮЖ@%")


def font_path(bold: bool) -> str | None:
    for path in FONT_FILES[bold]:
        if os.path.exists(path):
            return path
    return None


class Metrics:
    """Width of a string in pixels: exact through Pillow, estimated without it."""

    def __init__(self):
        self.fonts: dict[tuple[float, bool], object] = {}
        try:
            from PIL import ImageFont  # noqa: F401
            self.pillow = True
        except ImportError:
            self.pillow = False

    def font(self, size: float, bold: bool):
        key = (round(size, 1), bold)
        if key not in self.fonts:
            from PIL import ImageFont
            path = font_path(bold)
            self.fonts[key] = (ImageFont.truetype(path, round(size)) if path
                               else ImageFont.load_default(size=round(size)))
        return self.fonts[key]

    def width(self, text: str, size: float, bold: bool = False) -> float:
        if not text:
            return 0.0
        if self.pillow:
            try:
                return float(self.font(size, bold).getlength(text))
            except (OSError, ValueError):
                self.pillow = False
        return approx_width(text, size, bold)


def approx_width(text: str, size: float, bold: bool) -> float:
    total = 0.0
    for ch in text:
        if ch in NARROW:
            total += 0.32
        elif ch in WIDE:
            total += 0.92
        elif ch.isdigit():
            total += 0.56
        elif ch.isupper():
            total += 0.70
        else:
            total += 0.55
    return total * size * (1.05 if bold else 1.0)


def wrap(text: str, metrics: Metrics, size: float, bold: bool, max_w: float) -> list[str]:
    """Word wrapping; a word too long for the box is left as it is."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        probe = (current + " " + word).strip()
        if current and metrics.width(probe, size, bold) > max_w:
            lines.append(current)
            current = word
        else:
            current = probe
    if current:
        lines.append(current)
    return lines or [""]
