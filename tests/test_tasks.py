"""Сборка задач: секции правил и выключатели *_ENABLED."""

from __future__ import annotations

import logging

import pytest

from confluence_label_bot.__main__ import _build_tasks
from confluence_label_bot.config import Config

ALL_SECTIONS = """
rules:
  - name: r1
    source: "111"
    labels: ready
    target: "222"
mentions:
  - name: m1
    root: "333"
    labels: release
    column: Ответственный
    placeholder: Участники
graphs:
  - name: g1
    root: "444"
    labels: release
"""


def _names(config: Config) -> list[str]:
    # Клиент задачи при сборке только запоминают, запросов не делают.
    return [task.name for task in _build_tasks(config, client=object())]


def test_по_умолчанию_включены_все_настроенные_задачи(env, rules_yaml):
    rules_yaml(ALL_SECTIONS)
    assert _names(Config.load()) == ["moves", "mentions", "graphs"]


@pytest.mark.parametrize(
    "variable, task",
    [("MOVE_ENABLED", "moves"), ("MENTIONS_ENABLED", "mentions"), ("GRAPH_ENABLED", "graphs")],
)
def test_выключатель_убирает_задачу(env, rules_yaml, caplog, variable, task):
    rules_yaml(ALL_SECTIONS)
    env.setenv(variable, "false")

    with caplog.at_level(logging.INFO):
        names = _names(Config.load())

    assert task not in names
    assert len(names) == 2
    assert f"{variable}=false" in caplog.text


def test_включатель_не_заводит_задачу_без_правил(env, rules_yaml):
    rules_yaml()  # только rules
    env.setenv("GRAPH_ENABLED", "true")

    assert _names(Config.load()) == ["moves"]


def test_все_выключены_значит_задач_нет(env, rules_yaml):
    rules_yaml(ALL_SECTIONS)
    for variable in ("MOVE_ENABLED", "MENTIONS_ENABLED", "GRAPH_ENABLED"):
        env.setenv(variable, "0")

    assert _names(Config.load()) == []
