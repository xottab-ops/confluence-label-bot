"""Точка входа: запуск бота как демона.

Использование:
    python -m confluence_label_bot            # демон (по интервалу)
    python -m confluence_label_bot --once     # один проход и выход
    python -m confluence_label_bot --check     # проверка подключения и выход
"""

from __future__ import annotations

import argparse
import logging
import sys

from .bot import LabelMoverBot
from .client import ConfluenceClient, ConfluenceError
from .config import Config, ConfigError
from .health import HealthState, start_health_server


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _check(client: ConfluenceClient, config: Config, logger: logging.Logger) -> int:
    """Проверить доступность всех страниц, упомянутых в правилах."""
    ok = True
    try:
        logger.info("Бот работает под пользователем: %s", client.get_current_user())
    except ConfluenceError as exc:
        logger.error("Не удалось определить текущего пользователя: %s", exc)
        ok = False

    for rule in config.rules:
        logger.info("Правило %r (лейблы: %s):", rule.name, ", ".join(rule.labels))
        for page_id, role in [(rule.source, "источник"), (rule.target, "назначение")]:
            try:
                page = client.get_page(page_id)
            except ConfluenceError as exc:
                logger.error("  %-10s %s → недоступна: %s", role, page_id, exc)
                ok = False
                continue
            logger.info(
                "  %-10s %s %r (space=%s)", role, page.id, page.title, page.space_key
            )

    if not ok:
        logger.error("Проверка не пройдена.")
        return 1
    logger.info("Проверка успешна: правил %d.", len(config.rules))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="confluence_label_bot")
    parser.add_argument(
        "--once", action="store_true", help="Выполнить один проход и выйти"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Проверить конфигурацию, авторизацию и доступ к страницам, затем выйти",
    )
    parser.add_argument(
        "--rules",
        metavar="FILE",
        help="Путь к файлу правил (по умолчанию: RULES_FILE из .env либо rules.yaml)",
    )
    args = parser.parse_args(argv)

    try:
        config = Config.load(rules_file=args.rules)
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    _setup_logging(config.log_level)
    logger = logging.getLogger("confluence_label_bot")

    # Состояние здоровья есть всегда; heartbeat дёргается на каждом запросе к
    # Confluence — чтобы во время долгого обхода поддерева проба видела процесс
    # живым. Сам HTTP-сервер поднимаем только в режиме демона (ниже).
    health = HealthState(config.health_liveness_timeout)
    client = ConfluenceClient(config, heartbeat=health.beat)

    if args.check:
        return _check(client, config, logger)

    bot = LabelMoverBot(config, client, health=health)

    if args.once:
        try:
            bot.run_once()
        except ConfluenceError as exc:
            logger.error("Ошибка: %s", exc)
            return 1
        return 0

    start_health_server(health, config.health_port)

    try:
        bot.run_forever()
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())