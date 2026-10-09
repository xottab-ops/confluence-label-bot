"""Ошибки построения графа, которые задача пишет в лог словами, а не стеком.

Сетевые ошибки сюда не относятся: они приходят из client.py как ConfluenceError
и обрабатываются там же, где у остальных задач.
"""
from __future__ import annotations


class ReleaseGraphError(Exception):
    """Общий предок: страницу с такой ошибкой задача пропускает."""


class ParseError(ReleaseGraphError):
    """Страница есть, но таблицы раскатки на ней нет."""


class RenderError(ReleaseGraphError):
    """Граф не удалось нарисовать."""


class MissingPillow(RenderError):
    """Для PNG нужен Pillow; SVG рисуется и без него."""
