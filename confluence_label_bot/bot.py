"""Логика бота: по каждому правилу найти помеченные страницы и перенести их."""

from __future__ import annotations

import logging
import time

from .client import ConfluenceClient, ConfluenceError, Page
from .config import Config
from .rules import Rule

logger = logging.getLogger(__name__)


class LabelMoverBot:
    def __init__(self, config: Config, client: ConfluenceClient) -> None:
        self._cfg = config
        self._client = client

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
        pages = self._client.find_pages_with_labels_under(
            ancestor_ids=rule.sources,
            labels=rule.labels,
            space_key=rule.space_key,
        )

        # Целевая страница может лежать в поддереве источника — под саму себя
        # её переносить нельзя.
        pages = [p for p in pages if p.id != rule.target]

        if not pages:
            logger.debug(
                "Правило %r: страниц с лейблами %s в поддеревьях %s не найдено",
                rule.name,
                ", ".join(rule.labels),
                ", ".join(rule.sources),
            )
            return 0

        logger.info("Правило %r: найдено страниц к переносу: %d", rule.name, len(pages))
        moved = 0
        for page in pages:
            if self._move(rule, page):
                moved += 1
        logger.info("Правило %r: перенесено %d из %d", rule.name, moved, len(pages))
        return moved

    def _move(self, rule: Rule, page: Page) -> bool:
        if page.parent_id == rule.target:
            logger.debug(
                "Правило %r: страница %s (%s) уже под целевой, пропуск",
                rule.name,
                page.id,
                page.title,
            )
            return False

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

    def run_forever(self) -> None:
        cfg = self._cfg
        logger.info(
            "Демон запущен. Правил: %d, интервал=%dс, dry_run=%s",
            len(cfg.rules),
            cfg.poll_interval_seconds,
            cfg.dry_run,
        )
        for rule in cfg.rules:
            logger.info(
                "  %s: [%s] --(%s)--> %s",
                rule.name,
                ", ".join(rule.sources),
                ", ".join(rule.labels),
                rule.target,
            )
        while True:
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 — демон не должен падать на неожиданной ошибке
                logger.exception("Непредвиденная ошибка в цикле")
            time.sleep(cfg.poll_interval_seconds)