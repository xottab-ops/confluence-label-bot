"""Граф зависимостей релизов по странице раскатки — задача `graphs`.

Перенесено из проекта confluence-release-graph (пакет relgraph). Слои снизу
вверх, как и там:
  model      — Row и Plan, на них построено всё остальное
  parsing    — storage format → Row
  planning   — Row → Plan (волны, рёбра, точки входа)
  render     — Plan → PNG/SVG, встроенный движок
  publishing — картинка на место <graph_placeholder> и JSON плана под ней
  task       — ReleaseGraphBot, задача демона

Чего здесь нет из relgraph: CLI, собственного REST-клиента (используется общий
ConfluenceClient — ради общего троттлинга, см. daemon.py), Mermaid, mermaid-cli
и Kroki (см. render/__init__.py). Код перенесённых модулей не менялся, поэтому
их комментарии остались английскими, как в исходнике.
"""
