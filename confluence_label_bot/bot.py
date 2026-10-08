"""Задача переноса: по каждому правилу найти помеченные страницы и перенести их."""

from __future__ import annotations

import logging

from .client import ConfluenceClient, ConfluenceError, Page
from .config import Config
from .rules import Rule

logger = logging.getLogger(__name__)

TASK_NAME = "moves"


class LabelMoverBot:
    """Перенос страниц по лейблам — одна из задач демона.

    Своего цикла у задачи нет: расписанием, сном и health занимается Daemon,
    здесь только один проход по правилам.
    """

    def __init__(self, config: Config, client: ConfluenceClient) -> None:
        self._cfg = config
        self._client = client
        self.name = TASK_NAME
        self.cron = config.move_cron

    def describe(self) -> list[str]:
        return [
            f"{rule.name}: {rule.source} --({', '.join(rule.labels)})--> {rule.target}"
            for rule in self._cfg.rules
        ]

    def run_once(self) -> int:
        """Один проход по всем правилам.

        Возвращает суммарное количество фактически перенесённых страниц.
        """
        total = 0
        for rule in self._cfg.rules:
            try:
                total += self._apply_rule(rule)
            except ConfluenceError as exc:
                # Сбой одного правила не должен останавливать остальные.
                logger.error("Правило %r: ошибка, пропуск. %s", rule.name, exc)
        return total

    def _apply_rule(self, rule: Rule) -> int:
        found = self._client.find_pages_with_labels_under(
            ancestor_id=rule.source,
            labels=rule.labels,
            space_key=rule.space_key,
        )
        pages = [p for p in found if self._needs_move(rule, p)]

        logger.info(
            "Правило %r: найдено по лейблам: %d, из них к переносу: %d",
            rule.name,
            len(found),
            len(pages),
        )
        if not pages:
            return 0

        moved = 0
        for page in pages:
            if self._move(rule, page):
                moved += 1
        logger.info("Правило %r: перенесено %d из %d", rule.name, moved, len(pages))
        return moved

    @staticmethod
    def _needs_move(rule: Rule, page: Page) -> bool:
        """Нужно ли переносить страницу.

        Обход поддерева источника возвращает всё поддерево, а целевая страница
        может лежать внутри него — тогда в выборку попадают и уже перенесённые
        страницы. Отсеиваем их до логирования, чтобы в логах были только те,
        которые действительно переезжают.

        Проверяется всё поддерево target, а не только прямые дети: у перенесённой
        страницы её дочерние с тем же лейблом остаются под ней, и выдёргивать их
        наверх нельзя — это сломало бы иерархию.
        """
        return page.id != rule.target and not page.is_under(rule.target)

    def _move(self, rule: Rule, page: Page) -> bool:
        if self._cfg.dry_run:
            logger.info(
                "[DRY_RUN] Правило %r: перенёс бы %s %r под %s",
                rule.name,
                page.id,
                page.title,
                rule.target,
            )
            return False

        try:
            self._client.move_page(page, rule.target)
        except ConfluenceError as exc:
            logger.error(
                "Правило %r: не удалось перенести %s %r: %s", rule.name, page.id, page.title, exc
            )
            return False

        logger.info(
            "Правило %r: перенёс %s %r под %s", rule.name, page.id, page.title, rule.target
        )
        return True
