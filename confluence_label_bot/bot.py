"""Логика бота: по каждому правилу найти помеченные страницы и перенести их."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from croniter import croniter

from .client import ConfluenceClient, ConfluenceError, Page
from .config import Config
from .rules import Rule

logger = logging.getLogger(__name__)


def _humanize(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total} с"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} мин {seconds} с"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин"


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

        CQL по `ancestor` возвращает всё поддерево источника, а целевая страница
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

    def next_run_after(self, moment: datetime) -> datetime:
        """Время следующего запуска по расписанию, строго после moment."""
        return croniter(self._cfg.cron, moment).get_next(datetime)

    def run_forever(self) -> None:
        cfg = self._cfg
        logger.info(
            "Демон запущен. Правил: %d, расписание=%r, dry_run=%s",
            len(cfg.rules),
            cfg.cron,
            cfg.dry_run,
        )
        for rule in cfg.rules:
            logger.info(
                "  %s: %s --(%s)--> %s",
                rule.name,
                rule.source,
                ", ".join(rule.labels),
                rule.target,
            )
        while True:
            # Считаем от текущего момента, а не от прошлого срабатывания: если
            # проход затянулся дольше промежутка между запусками, пропущенные
            # сроки не копятся — просто идём к ближайшему будущему.
            now = datetime.now()
            next_run = self.next_run_after(now)
            logger.info(
                "Следующий запуск: %s (через %s)",
                next_run.strftime("%Y-%m-%d %H:%M:%S"),
                _humanize(next_run - now),
            )
            time.sleep(max(0.0, (next_run - now).total_seconds()))

            try:
                self.run_once()
            except Exception:  # noqa: BLE001 — демон не должен падать на неожиданной ошибке
                logger.exception("Непредвиденная ошибка в цикле")