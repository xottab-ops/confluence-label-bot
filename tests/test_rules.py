"""Валидация файла правил, включая пустую секцию rules."""

from __future__ import annotations

import pytest

from confluence_label_bot.rules import RulesError, load_rule_set


def load_moves(path):
    """Правила переноса из файла — то, что проверяет большинство тестов ниже."""
    return load_rule_set(path).moves


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_пустая_секция_rules_не_ошибка(tmp_path):
    """Установке может быть нужна только другая задача — это законный конфиг."""
    assert load_moves(_write(tmp_path, "rules: []\n")) == ()


def test_отсутствующая_секция_rules_не_ошибка(tmp_path):
    assert load_moves(_write(tmp_path, "mentions: []\n")) == ()


def test_совсем_пустой_файл_это_ошибка(tmp_path):
    with pytest.raises(RulesError, match="пуст"):
        load_moves(_write(tmp_path, ""))


def test_словарь_вместо_списка_правил_это_ошибка(tmp_path):
    with pytest.raises(RulesError, match="ожидался список правил"):
        load_moves(_write(tmp_path, "rules:\n  source: 111\n"))


def test_файл_не_найден(tmp_path):
    with pytest.raises(RulesError, match="не найден"):
        load_moves(tmp_path / "нет-такого.yaml")


def test_правило_читается_целиком(tmp_path):
    text = """
rules:
  - name: drafts
    source: 111
    labels: [ready, approved]
    target: 222
    space: DOCS
"""
    (rule,) = load_moves(_write(tmp_path, text))

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
        load_moves(_write(tmp_path, text))


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
        load_moves(_write(tmp_path, text))


def test_опечатка_в_названии_поля_это_ошибка(tmp_path):
    text = """
rules:
  - name: опечатка
    source: 111
    label: ready
    target: 222
"""
    with pytest.raises(RulesError, match="неизвестные поля: label"):
        load_moves(_write(tmp_path, text))


def test_список_в_source_это_ошибка(tmp_path):
    text = """
rules:
  - name: много
    source: [111, 222]
    labels: ready
    target: 333
"""
    with pytest.raises(RulesError, match="ровно одну страницу"):
        load_moves(_write(tmp_path, text))


# ── секция mentions ─────────────────────────────────────────────────────────
def test_правило_сбора_читается_целиком(tmp_path):
    text = """
mentions:
  - name: release-owners
    root: 555
    labels: [release, hotfix]
    column: Ответственный
    placeholder: Участники
    space: DOCS
"""
    (rule,) = load_rule_set(_write(tmp_path, text)).mentions

    assert rule.name == "release-owners"
    assert rule.root == "555"
    assert rule.labels == ("release", "hotfix")
    assert rule.columns == ("Ответственный",)
    assert rule.placeholder == "Участники"
    assert rule.space_key == "DOCS"


def test_столбец_можно_задать_списком(tmp_path):
    text = """
mentions:
  - root: 555
    labels: release
    column: [Ответственный, Ревьюер]
    placeholder: Участники
"""
    (rule,) = load_rule_set(_write(tmp_path, text)).mentions

    assert rule.columns == ("Ответственный", "Ревьюер")


def test_обе_секции_читаются_вместе(tmp_path):
    text = """
rules:
  - source: 111
    labels: ready
    target: 222

mentions:
  - root: 555
    labels: release
    column: Ответственный
    placeholder: Участники
"""
    rule_set = load_rule_set(_write(tmp_path, text))

    assert len(rule_set.moves) == 1
    assert len(rule_set.mentions) == 1
    assert bool(rule_set) is True


def test_только_секция_mentions_это_законный_конфиг(tmp_path):
    text = """
mentions:
  - root: 555
    labels: release
    column: Ответственный
    placeholder: Участники
"""
    rule_set = load_rule_set(_write(tmp_path, text))

    assert rule_set.moves == ()
    assert len(rule_set.mentions) == 1


def test_обе_секции_пусты_даёт_ложный_набор(tmp_path):
    """Сам файл валиден; «ни одной задачи» ловится при сборке задач."""
    rule_set = load_rule_set(_write(tmp_path, "rules: []\nmentions: []\n"))

    assert bool(rule_set) is False


def test_опечатка_в_названии_секции_это_ошибка(tmp_path):
    with pytest.raises(RulesError, match="неизвестные секции: mention"):
        load_rule_set(_write(tmp_path, "mention: []\n"))


def test_опечатка_в_поле_правила_сбора_это_ошибка(tmp_path):
    text = """
mentions:
  - root: 555
    labels: release
    columns: Ответственный
    placeholder: Участники
"""
    with pytest.raises(RulesError, match="неизвестные поля: columns"):
        load_rule_set(_write(tmp_path, text))


def test_незаданный_плейсхолдер_это_ошибка(tmp_path):
    text = """
mentions:
  - root: 555
    labels: release
    column: Ответственный
"""
    with pytest.raises(RulesError, match="'placeholder' не задано"):
        load_rule_set(_write(tmp_path, text))


def test_список_в_root_это_ошибка(tmp_path):
    text = """
mentions:
  - root: [555, 666]
    labels: release
    column: Ответственный
    placeholder: Участники
"""
    with pytest.raises(RulesError, match="ровно одну страницу"):
        load_rule_set(_write(tmp_path, text))


def test_один_плейсхолдер_в_одном_поддереве_дважды_это_ошибка(tmp_path):
    """Два таких правила затирали бы друг друга через проход."""
    text = """
mentions:
  - name: первое
    root: 555
    labels: release
    column: Ответственный
    placeholder: Участники
  - name: второе
    root: 555
    labels: hotfix
    column: Ревьюер
    placeholder: Участники
"""
    with pytest.raises(RulesError, match="в нескольких правилах сбора"):
        load_rule_set(_write(tmp_path, text))


def test_разные_плейсхолдеры_в_одном_поддереве_разрешены(tmp_path):
    text = """
mentions:
  - root: 555
    labels: release
    column: Ответственный
    placeholder: Участники
  - root: 555
    labels: release
    column: Ревьюер
    placeholder: Ревьюеры
"""
    assert len(load_rule_set(_write(tmp_path, text)).mentions) == 2


def test_повторяющиеся_имена_правил_сбора_это_ошибка(tmp_path):
    text = """
mentions:
  - name: одно
    root: 555
    labels: release
    column: Ответственный
    placeholder: Участники
  - name: одно
    root: 666
    labels: release
    column: Ответственный
    placeholder: Участники
"""
    with pytest.raises(RulesError, match="Повторяющиеся имена правил сбора"):
        load_rule_set(_write(tmp_path, text))
