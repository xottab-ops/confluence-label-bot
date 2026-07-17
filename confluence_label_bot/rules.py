"""Правила переноса: загрузка и валидация rules.yaml."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class RulesError(RuntimeError):
    """Ошибка в файле правил: не найден, некорректен или не проходит валидацию."""


@dataclass(frozen=True)
class Rule:
    """Одна связка «откуда → куда по лейблам».

    sources и labels могут содержать несколько значений: страница переносится,
    если лежит в поддереве любого из sources и имеет любой из labels.
    """

    name: str
    sources: tuple[str, ...]
    labels: tuple[str, ...]
    target: str
    space_key: str | None


def _as_str_list(value: Any, *, rule: str, field: str) -> tuple[str, ...]:
    """Привести значение к кортежу непустых строк.

    Допускает как одиночное значение, так и список: `sources: 111` равнозначно
    `sources: [111]`. Числа из YAML приводятся к строкам — ID страниц в API
    строковые.
    """
    if value is None:
        raise RulesError(f"Правило {rule!r}: поле {field!r} не задано")
    items = value if isinstance(value, list) else [value]
    if not items:
        raise RulesError(f"Правило {rule!r}: поле {field!r} не должно быть пустым")

    result: list[str] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, (str, int)):
            raise RulesError(
                f"Правило {rule!r}: поле {field!r} содержит недопустимое значение {item!r}"
            )
        text = str(item).strip()
        if not text:
            raise RulesError(f"Правило {rule!r}: поле {field!r} содержит пустое значение")
        if text not in result:
            result.append(text)
    return tuple(result)


def _parse_rule(data: Any, index: int) -> Rule:
    if not isinstance(data, dict):
        raise RulesError(f"Правило #{index + 1} должно быть словарём, получено: {type(data).__name__}")

    name = str(data.get("name") or f"rule-{index + 1}").strip()

    unknown = set(data) - {"name", "sources", "labels", "target", "space"}
    if unknown:
        raise RulesError(
            f"Правило {name!r}: неизвестные поля: {', '.join(sorted(unknown))}"
        )

    sources = _as_str_list(data.get("sources"), rule=name, field="sources")
    labels = _as_str_list(data.get("labels"), rule=name, field="labels")
    targets = _as_str_list(data.get("target"), rule=name, field="target")
    if len(targets) > 1:
        raise RulesError(
            f"Правило {name!r}: поле 'target' должно содержать ровно одну страницу "
            f"(страница не может лежать под несколькими родителями). "
            f"Для нескольких назначений заведите отдельные правила."
        )
    target = targets[0]

    if target in sources:
        raise RulesError(f"Правило {name!r}: 'target' не должен совпадать с 'sources'")

    space = data.get("space")
    space_key = str(space).strip() if space is not None and str(space).strip() else None

    return Rule(name=name, sources=sources, labels=labels, target=target, space_key=space_key)


def load_rules(path: str | Path) -> list[Rule]:
    """Прочитать и провалидировать файл правил."""
    file = Path(path)
    if not file.is_file():
        raise RulesError(f"Файл правил не найден: {file}")

    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RulesError(f"Некорректный YAML в {file}: {exc}") from exc

    if raw is None:
        raise RulesError(f"Файл правил пуст: {file}")

    # Допускаем как {rules: [...]}, так и просто список правил на верхнем уровне.
    items = raw.get("rules") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        raise RulesError(
            f"{file}: ожидался непустой список правил (ключ 'rules' либо список верхнего уровня)"
        )

    rules = [_parse_rule(item, i) for i, item in enumerate(items)]

    names = [r.name for r in rules]
    duplicates = {n for n in names if names.count(n) > 1}
    if duplicates:
        raise RulesError(f"Повторяющиеся имена правил: {', '.join(sorted(duplicates))}")

    logger.debug("Загружено правил: %d из %s", len(rules), file)
    return rules