"""Правила бота: загрузка и валидация rules.yaml.

Файл делится на секции по задачам: `rules:` — перенос страниц по лейблам,
`mentions:` — сбор @упоминаний из столбца таблицы в плейсхолдер, `graphs:` —
граф зависимостей релизов по таблице раскатки. Пустая или отсутствующая секция
означает, что соответствующая задача не настроена.
"""

from __future__ import annotations

import logging
import os
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


@dataclass(frozen=True)
class MentionRule:
    """Одно правило сбора @упоминаний.

    В поддереве root берутся страницы с любым из labels; на каждой из них
    упоминания из столбцов columns складываются в ячейку-плейсхолдер —
    вторую в строке, первая ячейка которой равна placeholder.
    """

    name: str
    root: str
    labels: tuple[str, ...]
    columns: tuple[str, ...]
    placeholder: str
    space_key: str | None


DEFAULT_GRAPH_PLACEHOLDER = "graph_placeholder"
DEFAULT_GRAPH_ATTACHMENT = "release-graph.png"
# Расширение вложения выбирает формат картинки — см. release_graph.render.
GRAPH_FORMATS = ("png", "svg")


@dataclass(frozen=True)
class GraphRule:
    """Одно правило графа зависимостей релизов.

    В поддереве root берутся страницы с любым из labels; на каждой по таблице
    раскатки рисуется граф и кладётся вложением attachment на место маркера
    placeholder, а под картинкой — JSON плана.
    """

    name: str
    root: str
    labels: tuple[str, ...]
    placeholder: str
    attachment: str
    width: int | None
    space_key: str | None


@dataclass(frozen=True)
class RuleSet:
    """Всё содержимое файла правил, разложенное по задачам."""

    moves: tuple[Rule, ...]
    mentions: tuple[MentionRule, ...]
    graphs: tuple[GraphRule, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.moves or self.mentions or self.graphs)


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


def _parse_mention_rule(data: Any, index: int) -> MentionRule:
    if not isinstance(data, dict):
        raise RulesError(
            f"Правило сбора #{index + 1} должно быть словарём, "
            f"получено: {type(data).__name__}"
        )

    name = str(data.get("name") or f"mentions-{index + 1}").strip()

    unknown = set(data) - {"name", "root", "labels", "column", "placeholder", "space"}
    if unknown:
        raise RulesError(
            f"Правило сбора {name!r}: неизвестные поля: {', '.join(sorted(unknown))}"
        )

    root = _single(data.get("root"), rule=name, field="root")
    labels = _as_str_list(data.get("labels"), rule=name, field="labels")
    # Несколько столбцов складываются в один плейсхолдер объединением.
    columns = _as_str_list(data.get("column"), rule=name, field="column")
    placeholder = _single(data.get("placeholder"), rule=name, field="placeholder")

    space = data.get("space")
    space_key = str(space).strip() if space is not None and str(space).strip() else None

    return MentionRule(
        name=name,
        root=root,
        labels=labels,
        columns=columns,
        placeholder=placeholder,
        space_key=space_key,
    )


def _validate_mention_rules(rules: list[MentionRule]) -> None:
    names = [rule.name for rule in rules]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise RulesError(
            f"Повторяющиеся имена правил сбора: {', '.join(sorted(duplicates))}"
        )

    # Два правила на одну пару «поддерево + плейсхолдер» затирали бы друг друга
    # через проход: кто отработал последним, тот и остался в ячейке.
    by_target: dict[tuple[str, str], list[str]] = {}
    for rule in rules:
        by_target.setdefault((rule.root, rule.placeholder), []).append(rule.name)
    conflicts = {key: names_ for key, names_ in by_target.items() if len(names_) > 1}
    if conflicts:
        details = "; ".join(
            f"{root} + {placeholder!r} → правила {', '.join(names_)}"
            for (root, placeholder), names_ in sorted(conflicts.items())
        )
        raise RulesError(
            f"Один и тот же плейсхолдер в одном поддереве указан "
            f"в нескольких правилах сбора: {details}"
        )


def _parse_graph_rule(data: Any, index: int) -> GraphRule:
    if not isinstance(data, dict):
        raise RulesError(
            f"Правило графа #{index + 1} должно быть словарём, "
            f"получено: {type(data).__name__}"
        )

    name = str(data.get("name") or f"graph-{index + 1}").strip()

    unknown = set(data) - {
        "name", "root", "labels", "placeholder", "attachment", "width", "space"
    }
    if unknown:
        raise RulesError(
            f"Правило графа {name!r}: неизвестные поля: {', '.join(sorted(unknown))}"
        )

    root = _single(data.get("root"), rule=name, field="root")
    labels = _as_str_list(data.get("labels"), rule=name, field="labels")
    placeholder = _single(
        data.get("placeholder") or DEFAULT_GRAPH_PLACEHOLDER, rule=name, field="placeholder"
    )
    attachment = _single(
        data.get("attachment") or DEFAULT_GRAPH_ATTACHMENT, rule=name, field="attachment"
    )
    ext = os.path.splitext(attachment)[1].lstrip(".").lower()
    if ext not in GRAPH_FORMATS:
        raise RulesError(
            f"Правило графа {name!r}: расширение вложения {attachment!r} выбирает "
            f"формат картинки и должно быть одним из: {', '.join(GRAPH_FORMATS)}"
        )

    width = data.get("width")
    if width is not None and (
        isinstance(width, bool) or not isinstance(width, int) or width <= 0
    ):
        raise RulesError(
            f"Правило графа {name!r}: 'width' должен быть положительным целым, "
            f"получено: {width!r}"
        )

    space = data.get("space")
    space_key = str(space).strip() if space is not None and str(space).strip() else None

    return GraphRule(
        name=name,
        root=root,
        labels=labels,
        placeholder=placeholder,
        attachment=attachment,
        width=width,
        space_key=space_key,
    )


def _validate_graph_rules(rules: list[GraphRule]) -> None:
    names = [rule.name for rule in rules]
    duplicates = {name for name in names if names.count(name) > 1}
    if duplicates:
        raise RulesError(
            f"Повторяющиеся имена правил графа: {', '.join(sorted(duplicates))}"
        )

    # Два правила на одно поддерево не должны делить ни вложение (затирали бы
    # картинку и JSON друг друга), ни маркер (первое съело бы его, а второе
    # не нашло бы ни маркера, ни своей картинки).
    for field, key in (
        ("attachment", lambda r: r.attachment.lower()),
        ("placeholder", lambda r: r.placeholder.lower()),
    ):
        by_target: dict[tuple[str, str], list[str]] = {}
        for rule in rules:
            by_target.setdefault((rule.root, key(rule)), []).append(rule.name)
        conflicts = {k: v for k, v in by_target.items() if len(v) > 1}
        if conflicts:
            details = "; ".join(
                f"{root} + {value!r} → правила {', '.join(names_)}"
                for (root, value), names_ in sorted(conflicts.items())
            )
            raise RulesError(
                f"Одно и то же поле {field!r} в одном поддереве указано "
                f"в нескольких правилах графа: {details}"
            )


def _parse_graphs(raw: Any, file: Path) -> list[GraphRule]:
    items = raw.get("graphs") if isinstance(raw, dict) else None
    if items is None:
        items = []
    if not isinstance(items, list):
        raise RulesError(
            f"{file}: секция 'graphs' должна быть списком, "
            f"получено: {type(items).__name__}"
        )

    rules = [_parse_graph_rule(item, i) for i, item in enumerate(items)]
    _validate_graph_rules(rules)
    return rules


def _read(path: str | Path) -> tuple[Any, Path]:
    file = Path(path)
    if not file.is_file():
        raise RulesError(f"Файл правил не найден: {file}")

    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RulesError(f"Некорректный YAML в {file}: {exc}") from exc

    if raw is None:
        raise RulesError(f"Файл правил пуст: {file}")
    return raw, file


def load_rule_set(path: str | Path) -> RuleSet:
    """Прочитать и провалидировать файл правил целиком, по всем задачам."""
    raw, file = _read(path)

    if isinstance(raw, dict):
        unknown = set(raw) - {"rules", "mentions", "graphs"}
        if unknown:
            raise RulesError(
                f"{file}: неизвестные секции: {', '.join(sorted(unknown))}. "
                f"Ожидались 'rules', 'mentions' и/или 'graphs'."
            )

    moves = _parse_rules(raw, file)
    mentions = _parse_mentions(raw, file)
    graphs = _parse_graphs(raw, file)

    logger.debug(
        "Загружено из %s: правил переноса %d, правил сбора %d, правил графа %d",
        file,
        len(moves),
        len(mentions),
        len(graphs),
    )
    return RuleSet(moves=tuple(moves), mentions=tuple(mentions), graphs=tuple(graphs))


def _parse_mentions(raw: Any, file: Path) -> list[MentionRule]:
    items = raw.get("mentions") if isinstance(raw, dict) else None
    if items is None:
        items = []
    if not isinstance(items, list):
        raise RulesError(
            f"{file}: секция 'mentions' должна быть списком, "
            f"получено: {type(items).__name__}"
        )

    rules = [_parse_mention_rule(item, i) for i, item in enumerate(items)]
    _validate_mention_rules(rules)
    return rules


def _parse_rules(raw: Any, file: Path) -> list[Rule]:
    # Допускаем как {rules: [...]}, так и просто список правил на верхнем уровне.
    items = raw.get("rules") if isinstance(raw, dict) else raw

    # Пустая или отсутствующая секция — не ошибка: у файла правил будут и другие
    # секции со своими задачами, и установке может быть нужна лишь часть из них.
    # «Не настроено ни одной задачи» проверяется при сборке задач, а не здесь.
    if items is None:
        items = []
    if not isinstance(items, list):
        raise RulesError(
            f"{file}: ожидался список правил (ключ 'rules' либо список верхнего уровня), "
            f"получено: {type(items).__name__}"
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

    return rules