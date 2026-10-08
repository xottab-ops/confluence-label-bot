"""Валидация файла правил, включая пустую секцию rules."""

from __future__ import annotations

import pytest

from confluence_label_bot.rules import RulesError, load_rules


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_пустая_секция_rules_не_ошибка(tmp_path):
    """Установке может быть нужна только другая задача — это законный конфиг."""
    assert load_rules(_write(tmp_path, "rules: []\n")) == []


def test_отсутствующая_секция_rules_не_ошибка(tmp_path):
    assert load_rules(_write(tmp_path, "mentions: []\n")) == []


def test_совсем_пустой_файл_это_ошибка(tmp_path):
    with pytest.raises(RulesError, match="пуст"):
        load_rules(_write(tmp_path, ""))


def test_словарь_вместо_списка_правил_это_ошибка(tmp_path):
    with pytest.raises(RulesError, match="ожидался список правил"):
        load_rules(_write(tmp_path, "rules:\n  source: 111\n"))


def test_файл_не_найден(tmp_path):
    with pytest.raises(RulesError, match="не найден"):
        load_rules(tmp_path / "нет-такого.yaml")


def test_правило_читается_целиком(tmp_path):
    text = """
rules:
  - name: drafts
    source: 111
    labels: [ready, approved]
    target: 222
    space: DOCS
"""
    (rule,) = load_rules(_write(tmp_path, text))

    assert rule.name == "drafts"
    # ID страниц в API строковые, даже если в YAML записаны числами.
    assert rule.source == "111"
    assert rule.target == "222"
    assert rule.labels == ("ready", "approved")
    assert rule.space_key == "DOCS"


def test_target_не_может_совпадать_с_source(tmp_path):
    text = """
rules:
  - name: петля
    source: 111
    labels: ready
    target: 111
"""
    with pytest.raises(RulesError, match="не должен совпадать"):
        load_rules(_write(tmp_path, text))


def test_один_source_в_двух_правилах_это_ошибка(tmp_path):
    text = """
rules:
  - name: первое
    source: 111
    labels: ready
    target: 222
  - name: второе
    source: 111
    labels: approved
    target: 333
"""
    with pytest.raises(RulesError, match="в нескольких правилах"):
        load_rules(_write(tmp_path, text))


def test_опечатка_в_названии_поля_это_ошибка(tmp_path):
    text = """
rules:
  - name: опечатка
    source: 111
    label: ready
    target: 222
"""
    with pytest.raises(RulesError, match="неизвестные поля: label"):
        load_rules(_write(tmp_path, text))


def test_список_в_source_это_ошибка(tmp_path):
    text = """
rules:
  - name: много
    source: [111, 222]
    labels: ready
    target: 333
"""
    with pytest.raises(RulesError, match="ровно одну страницу"):
        load_rules(_write(tmp_path, text))
