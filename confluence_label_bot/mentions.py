"""Задача сбора @упоминаний: из столбца таблицы в ячейку-плейсхолдер.

На каждой отобранной странице бот читает упоминания из столбцов с заданными
заголовками и складывает их во вторую ячейку строки, первая ячейка которой
равна имени плейсхолдера. Разбор и правка тела — в storage.py.

Ключевое свойство — не писать, когда нечего менять. Задача ходит по cron, и
перезапись без изменений плодила бы версии страницы и письма «вас упомянули»
всем из таблицы на каждом проходе.
"""

from __future__ import annotations

import logging

from . import storage
from .client import ConfluenceClient, ConfluenceConflict, ConfluenceError
from .config import Config
from .rules import MentionRule

logger = logging.getLogger(__name__)

TASK_NAME = "mentions"


class MentionCollectorBot:
    """Сбор упоминаний в плейсхолдер — одна из задач демона."""

    def __init__(self, config: Config, client: ConfluenceClient) -> None:
        self._cfg = config
        self._client = client
        self.name = TASK_NAME
        self.cron = config.mentions_cron

    def describe(self) -> list[str]:
        return [
            f"{rule.name}: {rule.root} ({', '.join(rule.labels)}) "
            f"столбец {', '.join(rule.columns)!r} --> плейсхолдер {rule.placeholder!r}"
            for rule in self._cfg.mention_rules
        ]

    def run_once(self) -> int:
        """Один проход по всем правилам сбора.

        Возвращает количество фактически изменённых страниц.
        """
        total = 0
        for rule in self._cfg.mention_rules:
            try:
                total += self._apply_rule(rule)
            except ConfluenceError as exc:
                # Сбой одного правила не должен останавливать остальные.
                logger.error("Правило сбора %r: ошибка, пропуск. %s", rule.name, exc)
        return total

    def _apply_rule(self, rule: MentionRule) -> int:
        pages = self._client.find_pages_with_labels_under(
            ancestor_id=rule.root,
            labels=rule.labels,
            space_key=rule.space_key,
        )
        logger.info(
            "Правило сбора %r: страниц по лейблам: %d", rule.name, len(pages)
        )

        updated = 0
        for page in pages:
            try:
                if self._process(rule, page.id):
                    updated += 1
            except ConfluenceError as exc:
                # Одна битая страница не должна срывать проход по остальным.
                logger.error(
                    "Правило сбора %r: страница %s — ошибка, пропуск. %s",
                    rule.name,
                    page.id,
                    exc,
                )

        logger.info("Правило сбора %r: обновлено страниц: %d", rule.name, updated)
        return updated

    def _process(self, rule: MentionRule, page_id: str, *, retry: bool = True) -> bool:
        """Обработать одну страницу. True — страница изменена.

        Тело читается здесь же, непосредственно перед записью: так версия
        заведомо свежая, и правка не наступает на результат задачи переноса,
        которая могла поднять версию этой же странице в этом же проходе.
        """
        body = self._client.get_page_body(page_id)
        tables = storage.parse_tables(body.storage)
        title = body.page.title

        placeholders = storage.find_placeholders(tables, rule.placeholder)
        if not placeholders:
            # У страницы есть лейбл правила, значит её выбрали намеренно, а
            # приёмника нет — это опечатка в разметке или в конфиге.
            logger.warning(
                "Правило сбора %r: %s %r — нет строки-плейсхолдера %r, пропуск",
                rule.name,
                page_id,
                title,
                rule.placeholder,
            )
            return False
        if len(placeholders) > 1:
            logger.warning(
                "Правило сбора %r: %s %r — строк с плейсхолдером %r несколько (%d), "
                "беру первую",
                rule.name,
                page_id,
                title,
                rule.placeholder,
                len(placeholders),
            )
        placeholder = placeholders[0]

        if not storage.has_columns(tables, rule.columns):
            logger.warning(
                "Правило сбора %r: %s %r — нет столбца %s, пропуск",
                rule.name,
                page_id,
                title,
                ", ".join(repr(column) for column in rule.columns),
            )
            return False

        # exclude: если приёмник оказался в том же столбце, без этого бот
        # вычитал бы собственный прошлый результат и считал его данными.
        mentions = storage.collect_mentions(tables, rule.columns, exclude=placeholder)

        if storage.same_mentions(placeholder, mentions):
            logger.debug(
                "Правило сбора %r: %s %r — плейсхолдер уже актуален",
                rule.name,
                page_id,
                title,
            )
            return False

        new_storage = storage.splice(
            body.storage, placeholder, storage.render_mentions(mentions)
        )
        people = self._names(mentions)

        if self._cfg.mentions_dry_run:
            logger.info(
                "[DRY_RUN] Правило сбора %r: записал бы в %s %r плейсхолдер %r: %s",
                rule.name,
                page_id,
                title,
                rule.placeholder,
                people,
            )
            return False

        try:
            self._client.update_page_body(body.page, new_storage)
        except ConfluenceConflict as exc:
            if not retry:
                logger.error(
                    "Правило сбора %r: %s %r — конфликт версий и после повтора: %s",
                    rule.name,
                    page_id,
                    title,
                    exc,
                )
                return False
            # Страницу изменили между чтением и записью: повторять тот же PUT
            # бессмысленно, нужно перечитать тело и пересчитать правку.
            logger.info(
                "Правило сбора %r: %s %r — конфликт версий, перечитываю и повторяю",
                rule.name,
                page_id,
                title,
            )
            return self._process(rule, page_id, retry=False)

        logger.info(
            "Правило сбора %r: записал в %s %r плейсхолдер %r: %s",
            rule.name,
            page_id,
            title,
            rule.placeholder,
            people,
        )
        return True

    def _names(self, mentions: list[storage.Mention]) -> str:
        """Имена для лога. Опознаватели вроде ff8080815… в логе бесполезны."""
        if not mentions:
            return "(пусто)"
        return ", ".join(
            self._client.user_display_name(mention.key) for mention in mentions
        )
