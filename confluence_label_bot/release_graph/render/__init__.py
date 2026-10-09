"""Картинка графа — только встроенным движком (раскладка + холст).

Из исходного relgraph намеренно не перенесены mermaid-cli и Kroki. Первому в
контейнере нужен Node.js с Chromium, а публичный kroki.io означал бы, что
названия команд и ключи Jira уходят во внешний сервис. Демону хватает
встроенного движка: SVG рисуется на голой stdlib, PNG — через Pillow.
"""
from __future__ import annotations

import os

from ..errors import MissingPillow, RenderError
from ..model import Plan, Row, row_state
from .builtin import render as render_builtin
from .text import font_path

__all__ = ["MEDIA_TYPES", "MissingPillow", "RenderError", "font_path",
           "picture_format", "render_image"]

MEDIA_TYPES = {"png": "image/png", "svg": "image/svg+xml"}


def picture_format(filename: str) -> str:
    """Формат по расширению имени вложения: graph.svg → svg."""
    ext = os.path.splitext(filename)[1].lstrip(".").lower()
    if ext not in MEDIA_TYPES:
        raise RenderError(f"Неизвестный формат картинки {filename!r}: поддерживаются "
                          f"только {', '.join(MEDIA_TYPES)}")
    return ext


def render_image(rows: list[Row], plan: Plan, *, fmt: str, scale: float = 2.0,
                 background: str = "white", legend: bool = True) -> bytes:
    return render_builtin(rows, plan, row_state, fmt=fmt, scale=scale,
                          background=background, legend=legend)
