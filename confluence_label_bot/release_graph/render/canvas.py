"""
Two canvases with the same interface: SVG and PNG.

The drawing code (draw.py) has no idea where its output lands, so both the
vector and the raster come out of the very same rect/polygon/curve/text calls.
"""
from __future__ import annotations

import math

from ..errors import MissingPillow
from .text import SVG_FONT, Metrics

NL = chr(10)


def bezier_points(curve, steps: int = 22) -> list[tuple[float, float]]:
    (x0, y0), (x1, y1), (x2, y2), (x3, y3) = curve
    out = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        out.append((u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
                    u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3))
    return out


class SvgCanvas:
    def __init__(self, width: float, height: float, background: str, **_ignored):
        self.width, self.height, self.background = width, height, background
        self.parts: list[str] = []

    @staticmethod
    def esc(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    @staticmethod
    def dash_attr(dash) -> str:
        return f' stroke-dasharray="{dash[0]} {dash[1]}"' if dash else ""

    def rect(self, x, y, w, h, *, fill, stroke, width, radius=6.0):
        self.parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" '
            f'rx="{radius:.1f}" fill="{fill}" stroke="{stroke}" stroke-width="{width:.1f}"/>')

    def polygon(self, points, *, fill, stroke, width):
        coords = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        self.parts.append(f'<polygon points="{coords}" fill="{fill}" stroke="{stroke}" '
                          f'stroke-width="{width:.1f}"/>')

    def curve(self, curves, *, stroke, width, dash=None):
        (x0, y0) = curves[0][0]
        path = [f"M {x0:.1f} {y0:.1f}"]
        for _p0, (cx1, cy1), (cx2, cy2), (x3, y3) in curves:
            path.append(f"C {cx1:.1f} {cy1:.1f} {cx2:.1f} {cy2:.1f} {x3:.1f} {y3:.1f}")
        self.parts.append(f'<path d="{" ".join(path)}" fill="none" stroke="{stroke}" '
                          f'stroke-width="{width:.1f}"{self.dash_attr(dash)} '
                          f'stroke-linecap="round"/>')

    def text(self, x, y, text, *, size, fill, bold=False):
        weight = ' font-weight="600"' if bold else ""
        self.parts.append(
            f'<text x="{x:.1f}" y="{y:.1f}" font-family="{SVG_FONT}" font-size="{size:.1f}"'
            f'{weight} fill="{fill}" text-anchor="middle" dominant-baseline="central">'
            f'{self.esc(text)}</text>')

    def result(self) -> bytes:
        head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width:.0f}" '
                f'height="{self.height:.0f}" viewBox="0 0 {self.width:.0f} {self.height:.0f}">')
        body = NL.join(["  " + p for p in self.parts])
        return NL.join([
            '<?xml version="1.0" encoding="UTF-8"?>', head,
            f'  <rect width="100%" height="100%" fill="{self.background}"/>',
            body, "</svg>", "",
        ]).encode("utf-8")


class PngCanvas:
    """The same through Pillow. We draw big and shrink — that is what smooths the edges."""

    SUPERSAMPLE = 2

    def __init__(self, width: float, height: float, background: str,
                 scale: float = 2.0, metrics: Metrics | None = None):
        try:
            from PIL import Image, ImageDraw
        except ImportError as e:
            raise MissingPillow("PNG needs Pillow: pip install pillow "
                                "(or use an .svg attachment name)") from e
        self.metrics = metrics or Metrics()
        self.k = scale * self.SUPERSAMPLE
        self.size = (max(1, round(width * scale)), max(1, round(height * scale)))
        self.image = Image.new("RGB", (max(1, round(width * self.k)),
                                       max(1, round(height * self.k))), background)
        self.draw = ImageDraw.Draw(self.image)
        self._Image = Image

    def _p(self, points):
        return [(x * self.k, y * self.k) for x, y in points]

    def _pen(self, width: float) -> int:
        return max(1, round(width * self.k))

    def rect(self, x, y, w, h, *, fill, stroke, width, radius=6.0):
        self.draw.rounded_rectangle(
            [x * self.k, y * self.k, (x + w) * self.k, (y + h) * self.k],
            radius=radius * self.k, fill=fill, outline=stroke, width=self._pen(width))

    def polygon(self, points, *, fill, stroke, width):
        self.draw.polygon(self._p(points), fill=fill, outline=stroke, width=self._pen(width))

    def curve(self, curves, *, stroke, width, dash=None):
        line: list[tuple[float, float]] = []
        for curve in curves:
            part = bezier_points(curve)
            line += part[1:] if line else part
        if dash:
            for a, b in self.dashes(line, dash):
                self.draw.line(self._p([a, b]), fill=stroke, width=self._pen(width))
        else:
            self.draw.line(self._p(line), fill=stroke, width=self._pen(width), joint="curve")

    @staticmethod
    def dashes(line, dash):
        """Cut a polyline into dashes: Pillow has no dashed lines."""
        on, off = dash
        segments, drawing, left = [], True, on
        for (x0, y0), (x1, y1) in zip(line, line[1:]):
            length = math.hypot(x1 - x0, y1 - y0)
            done = 0.0
            while done < length:
                step = min(left, length - done)
                t0, t1 = done / length, (done + step) / length
                if drawing:
                    segments.append(((x0 + (x1 - x0) * t0, y0 + (y1 - y0) * t0),
                                     (x0 + (x1 - x0) * t1, y0 + (y1 - y0) * t1)))
                done += step
                left -= step
                if left <= 1e-9:
                    drawing = not drawing
                    left = on if drawing else off
        return segments

    def text(self, x, y, text, *, size, fill, bold=False):
        self.draw.text((x * self.k, y * self.k), text, font=self.metrics.font(size * self.k, bold),
                       fill=fill, anchor="mm")

    def result(self) -> bytes:
        import io

        image = self.image.resize(self.size, self._Image.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()


CANVASES = {"svg": SvgCanvas, "png": PngCanvas}
