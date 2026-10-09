"""Задача графа зависимостей релизов — одна из задач демона.

На каждой отобранной по лейблу странице: таблица раскатки → план по волнам →
картинка. Картинка заливается вложением и встаёт на место маркера, под ней —
JSON плана. Порядок тот же, что у publish_picture в relgraph: прочитать
текущее тело (чтобы не затереть чужие правки), залить вложение, сохранить тело.

Отличие от relgraph одно, и оно из-за cron: relgraph на каждом запуске
заливает новую версию вложения. Здесь страница пропускается целиком, если
JSON на ней совпадает с новым планом, а тело менять не нужно, — иначе каждый
проход плодил бы версию вложения. План однозначно задаёт картинку, поэтому
совпавший JSON значит и совпавшую картинку.
"""
from __future__ import annotations

import logging

from ..client import ConfluenceClient, ConfluenceConflict, ConfluenceError
from ..config import Config
from ..rules import GraphRule
from . import publishing
from .errors import ParseError, RenderError
from .parsing import parse_rows
from .planning import build_plan
from .render import MEDIA_TYPES, font_path, picture_format, render_image

logger = logging.getLogger(__name__)

TASK_NAME = "graphs"


class ReleaseGraphBot:
    """Граф зависимостей релизов на страницах раскатки."""

    def __init__(self, config: Config, client: ConfluenceClient) -> None:
        self._cfg = config
        self._client = client
        self.name = TASK_NAME
        self.cron = config.graph_cron

        # Без TTF-шрифта Pillow берёт встроенный, а в нём нет кириллицы —
        # русские названия команд превратились бы в квадраты. В контейнере это
        # обычное дело, поэтому говорим об этом сразу, а не после первой картинки.
        if any(picture_format(r.attachment) == "png" for r in config.graph_rules):
            if font_path(False) is None:
                logger.warning(
                    "Задача %s: не найден ни один TTF-шрифт (Segoe UI, Arial, DejaVu) — "
                    "кириллица на PNG не отрисуется. В образе Debian/Ubuntu: "
                    "apt-get install fonts-dejavu-core",
                    TASK_NAME,
                )

    def describe(self) -> list[str]:
        return [
            f"{rule.name}: {rule.root} ({', '.join(rule.labels)}) "
            f"маркер {rule.placeholder!r} --> {rule.attachment}"
            for rule in self._cfg.graph_rules
        ]

    def run_once(self) -> int:
        """Один проход по всем правилам графа. Возвращает число изменённых страниц."""
        total = 0
        for rule in self._cfg.graph_rules:
            try:
                total += self._apply_rule(rule)
            except ConfluenceError as exc:
                # Сбой одного правила не должен останавливать остальные.
                logger.error("Правило графа %r: ошибка, пропуск. %s", rule.name, exc)
        return total

    def _apply_rule(self, rule: GraphRule) -> int:
        pages = self._client.find_pages_with_labels_under(
            ancestor_id=rule.root,
            labels=rule.labels,
            space_key=rule.space_key,
        )
        logger.info("Правило графа %r: страниц по лейблам: %d", rule.name, len(pages))

        updated = 0
        for page in pages:
            try:
                if self._process(rule, page.id):
                    updated += 1
            except ConfluenceError as exc:
                # Одна битая страница не должна срывать проход по остальным.
                logger.error(
                    "Правило графа %r: страница %s — ошибка, пропуск. %s",
                    rule.name,
                    page.id,
                    exc,
                )

        logger.info("Правило графа %r: обновлено страниц: %d", rule.name, updated)
        return updated

    def _process(
        self,
        rule: GraphRule,
        page_id: str,
        *,
        retry: bool = True,
        uploaded: bytes | None = None,
    ) -> bool:
        """Обработать одну страницу. True — страница или вложение изменены.

        uploaded — картинка, уже залитая в этом же проходе до конфликта версий:
        если после перечитывания она не изменилась, второй раз не льём.
        """
        body = self._client.get_page_body(page_id)
        title = body.page.title

        try:
            rows = parse_rows(body.storage)
        except ParseError as exc:
            # Лейбл на странице есть, значит её выбрали намеренно, а таблицы
            # нет — опечатка в заголовках столбцов или лейбл не на той странице.
            logger.warning(
                "Правило графа %r: %s %r — %s, пропуск", rule.name, page_id, title, exc
            )
            return False

        plan = build_plan(rows)
        for message in plan.errors:
            logger.warning(
                "Правило графа %r: %s %r — ошибка плана: %s", rule.name, page_id, title, message
            )
        for message in plan.warnings:
            logger.debug(
                "Правило графа %r: %s %r — %s", rule.name, page_id, title, message
            )

        insertion = publishing.insert_image(
            body.storage, rule.attachment, rule.placeholder, rule.width
        )
        if insertion.kind == publishing.MISSING:
            logger.warning(
                "Правило графа %r: %s %r — нет ни маркера <%s>, ни картинки %s; "
                "поставьте <%s> туда, где нужен граф. Пропуск",
                rule.name,
                page_id,
                title,
                rule.placeholder,
                rule.attachment,
                rule.placeholder,
            )
            return False

        data = publishing.plan_data(rows, plan)
        storage, data_changed = publishing.insert_json(insertion.storage, rule.attachment, data)
        body_changed = storage != body.storage

        if not data_changed and not body_changed:
            logger.debug(
                "Правило графа %r: %s %r — граф уже актуален", rule.name, page_id, title
            )
            return False

        fmt = picture_format(rule.attachment)
        try:
            picture = render_image(rows, plan, fmt=fmt)
        except RenderError as exc:
            logger.error(
                "Правило графа %r: %s %r — не удалось нарисовать граф: %s",
                rule.name,
                page_id,
                title,
                exc,
            )
            return False

        summary = (
            f"строк {len(rows)}, связей {len(plan.edges)}, волн {len(plan.waves)}"
        )
        if self._cfg.graph_dry_run:
            logger.info(
                "[DRY_RUN] Правило графа %r: залил бы в %s %r вложение %s (%d байт, %s)%s",
                rule.name,
                page_id,
                title,
                rule.attachment,
                len(picture),
                summary,
                "; маркер заменил бы картинкой" if insertion.kind == publishing.PLACEHOLDER
                else "",
            )
            return False

        if picture != uploaded:
            outcome = self._client.put_attachment(
                page_id, rule.attachment, picture, MEDIA_TYPES[fmt], publishing.COMMENT
            )
            logger.info(
                "Правило графа %r: %s %r — вложение %s (%d байт): %s",
                rule.name,
                page_id,
                title,
                rule.attachment,
                len(picture),
                outcome,
            )

        if body_changed:
            try:
                self._client.update_page_body(body.page, storage)
            except ConfluenceConflict as exc:
                if not retry:
                    logger.error(
                        "Правило графа %r: %s %r — конфликт версий и после повтора: %s",
                        rule.name,
                        page_id,
                        title,
                        exc,
                    )
                    return False
                # Страницу изменили между чтением и записью: нужно перечитать
                # тело и пересчитать правку — таблица могла измениться тоже.
                logger.info(
                    "Правило графа %r: %s %r — конфликт версий, перечитываю и повторяю",
                    rule.name,
                    page_id,
                    title,
                )
                return self._process(rule, page_id, retry=False, uploaded=picture)

        logger.info(
            "Правило графа %r: обновил %s %r: %s%s",
            rule.name,
            page_id,
            title,
            summary,
            f", маркер <{rule.placeholder}> заменён картинкой"
            if insertion.kind == publishing.PLACEHOLDER
            else "",
        )
        return True
