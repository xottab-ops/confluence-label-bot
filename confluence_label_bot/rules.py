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

    source и target — ровно по одной странице. labels могут содержать несколько
    значений: страница переносится, если лежит в поддереве source и имеет любой
    из labels.
    """

    name: str
    source: str
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


def _single(value: Any, *, rule: str, field: str) -> str:
    """Привести значение к одной непустой строке, отвергая списки.

    source и target — всегда одна страница: в Confluence страница не может
    лежать под несколькими родителями, а несколько источников разводятся
    отдельными правилами.
    """
    if isinstance(value, list):
        raise RulesError(
            f"Правило {rule!r}: поле {field!r} должно содержать ровно одну страницу, "
            f"а не список. Заведите отдельное правило на каждую."
        )
    values = _as_str_list(value, rule=rule, field=field)
    return values[0]


def _parse_rule(data: Any, index: int) -> Rule:
    if not isinstance(data, dict):
        raise RulesError(f"Правило #{index + 1} должно быть словарём, получено: {type(data).__name__}")

    name = str(data.get("name") or f"rule-{index + 1}").strip()

    unknown = set(data) - {"name", "source", "labels", "target", "space"}
    if unknown:
        raise RulesError(
            f"Правило {name!r}: неизвестные поля: {', '.join(sorted(unknown))}"
        )

    labels = _as_str_list(data.get("labels"), rule=name, field="labels")
    source = _single(data.get("source"), rule=name, field="source")
    target = _single(data.get("target"), rule=name, field="target")

    if target == source:
        raise RulesError(f"Правило {name!r}: 'target' не должен совпадать с 'source'")

    space = data.get("space")
    space_key = str(space).strip() if space is not None and str(space).strip() else None

    return Rule(name=name, source=source, labels=labels, target=target, space_key=space_key)


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

    # source уникален глобально: одна страница-источник обслуживается ровно
    # одним правилом, иначе непонятно, куда переносить подходящую страницу.
    by_source: dict[str, list[str]] = {}
    for rule in rules:
        by_source.setdefault(rule.source, []).append(rule.name)
    conflicts = {src: names_ for src, names_ in by_source.items() if len(names_) > 1}
    if conflicts:
        details = "; ".join(
            f"{src} → правила {', '.join(names_)}" for src, names_ in sorted(conflicts.items())
        )
        raise RulesError(f"Один и тот же 'source' указан в нескольких правилах: {details}")

    logger.debug("Загружено правил: %d из %s", len(rules), file)
    return rules