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


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


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
    args = parser.parse_args(argv)

    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    _setup_logging(config.log_level)
    logger = logging.getLogger("confluence_label_bot")

    client = ConfluenceClient(config)

    if args.check:
        try:
            src = client.get_page(config.source_page_id)
            dst = client.get_page(config.target_page_id)
        except ConfluenceError as exc:
            logger.error("Проверка не пройдена: %s", exc)
            return 1
        logger.info("Источник:   %s %r (space=%s)", src.id, src.title, src.space_key)
        logger.info("Назначение: %s %r (space=%s)", dst.id, dst.title, dst.space_key)
        logger.info("Проверка успешна.")
        return 0

    bot = LabelMoverBot(config, client)

    if args.once:
        try:
            bot.run_once()
        except ConfluenceError as exc:
            logger.error("Ошибка: %s", exc)
            return 1
        return 0

    try:
        bot.run_forever()
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())