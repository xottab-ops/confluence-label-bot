"""Логика бота: поиск помеченных страниц и перенос под целевую страницу."""

from __future__ import annotations

import logging
import time

from .client import ConfluenceClient, ConfluenceError, Page
from .config import Config

logger = logging.getLogger(__name__)


class LabelMoverBot:
    def __init__(self, config: Config, client: ConfluenceClient) -> None:
        self._cfg = config
        self._client = client

    def run_once(self) -> int:
        """Один проход: найти и перенести подходящие страницы.

        Возвращает количество фактически перенесённых страниц.
        """
        cfg = self._cfg
        pages = self._client.find_pages_with_label_under(
            space_key=cfg.space_key,
            ancestor_id=cfg.source_page_id,
            label=cfg.move_label,
        )

        # Целевая страница уже может лежать в поддереве источника — её саму
        # переносить под саму себя нельзя.
        pages = [p for p in pages if p.id != cfg.target_page_id]

        if not pages:
            logger.debug(
                "Страниц с лейблом %r в поддереве %s не найдено",
                cfg.move_label,
                cfg.source_page_id,
            )
            return 0

        logger.info("Найдено страниц к переносу: %d", len(pages))
        moved = 0
        for page in pages:
            if self._move(page):
                moved += 1
        logger.info("Перенесено страниц: %d из %d", moved, len(pages))
        return moved

    def _move(self, page: Page) -> bool:
        cfg = self._cfg
        if page.parent_id == cfg.target_page_id:
            logger.debug("Страница %s (%s) уже под целевой, пропуск", page.id, page.title)
            return False

        if cfg.dry_run:
            logger.info(
                "[DRY_RUN] Перенёс бы %s %r под %s",
                page.id,
                page.title,
                cfg.target_page_id,
            )
            return False

        try:
            self._client.move_page(page, cfg.target_page_id)
        except ConfluenceError as exc:
            logger.error("Не удалось перенести %s %r: %s", page.id, page.title, exc)
            return False

        logger.info("Перенёс %s %r под %s", page.id, page.title, cfg.target_page_id)
        return True

    def run_forever(self) -> None:
        cfg = self._cfg
        logger.info(
            "Демон запущен. Источник=%s, назначение=%s, лейбл=%r, интервал=%dс, dry_run=%s",
            cfg.source_page_id,
            cfg.target_page_id,
            cfg.move_label,
            cfg.poll_interval_seconds,
            cfg.dry_run,
        )
        while True:
            try:
                self.run_once()
            except ConfluenceError as exc:
                logger.error("Ошибка цикла: %s", exc)
            except Exception:  # noqa: BLE001 — демон не должен падать на неожиданной ошибке
                logger.exception("Непредвиденная ошибка в цикле")
            time.sleep(cfg.poll_interval_seconds)