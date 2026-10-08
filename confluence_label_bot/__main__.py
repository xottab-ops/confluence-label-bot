"""Точка входа: запуск бота как демона.

Использование:
    python -m confluence_label_bot                    # демон (по расписаниям задач)
    python -m confluence_label_bot --once             # один проход всех задач и выход
    python -m confluence_label_bot --only mentions    # только одна задача (moves/mentions)
    python -m confluence_label_bot --check            # проверка подключения и выход
"""

from __future__ import annotations

import argparse
import logging
import sys

from .bot import LabelMoverBot
from .client import ConfluenceClient, ConfluenceError
from .config import Config, ConfigError
from .daemon import Daemon, ScheduledTask
from .health import HealthState, start_health_server
from .mentions import MentionCollectorBot


def _setup_logging(level: str) -> None:
    # Консоль Windows по умолчанию не в UTF-8, и первый же символ вне её
    # кодовой страницы (а в сообщениях есть и «→», и кавычки-ёлочки) валит
    # обработчик логов с UnicodeEncodeError. Логи не должны падать из-за
    # кодировки терминала, поэтому непредставимое заменяется, а не ломает вывод.
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="backslashreplace")

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _build_tasks(config: Config, client: ConfluenceClient) -> list[ScheduledTask]:
    """Собрать задачи, под которые что-то настроено.

    Задача регистрируется, только если её секция в файле правил непустая:
    установке может быть нужна лишь часть задач, и держать в расписании
    задачу без правил бессмысленно.
    """
    tasks: list[ScheduledTask] = []
    if config.rules:
        tasks.append(LabelMoverBot(config, client))
    # Порядок регистрации — порядок выполнения при совпадении сроков: сбор
    # упоминаний идёт после переносов, по уже устоявшемуся дереву.
    if config.mention_rules:
        tasks.append(MentionCollectorBot(config, client))
    return tasks


def _select(
    tasks: list[ScheduledTask], only: str | None
) -> list[ScheduledTask] | None:
    """Отфильтровать задачи по --only. None — имя не опознано."""
    if not only:
        return tasks
    chosen = [task for task in tasks if task.name == only]
    if not chosen:
        available = ", ".join(task.name for task in tasks)
        print(
            f"Неизвестная задача в --only: {only!r}. Настроены: {available}",
            file=sys.stderr,
        )
        return None
    return chosen


def _check(
    client: ConfluenceClient,
    config: Config,
    tasks: list[ScheduledTask],
    logger: logging.Logger,
) -> int:
    """Проверить расписания и доступность всех страниц, упомянутых в правилах."""
    ok = True
    try:
        logger.info("Бот работает под пользователем: %s", client.get_current_user())
    except ConfluenceError as exc:
        logger.error("Не удалось определить текущего пользователя: %s", exc)
        ok = False

    for task in tasks:
        logger.info("Задача %s — расписание %r", task.name, task.cron)

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

    for rule in config.mention_rules:
        logger.info(
            "Правило сбора %r (лейблы: %s, столбец: %s → плейсхолдер %r):",
            rule.name,
            ", ".join(rule.labels),
            ", ".join(rule.columns),
            rule.placeholder,
        )
        try:
            page = client.get_page(rule.root)
        except ConfluenceError as exc:
            logger.error("  %-10s %s → недоступна: %s", "корень", rule.root, exc)
            ok = False
        else:
            logger.info(
                "  %-10s %s %r (space=%s)", "корень", page.id, page.title, page.space_key
            )

    if not ok:
        logger.error("Проверка не пройдена.")
        return 1
    logger.info(
        "Проверка успешна: задач %d, правил переноса %d, правил сбора %d.",
        len(tasks),
        len(config.rules),
        len(config.mention_rules),
    )
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
        "--only",
        metavar="TASK",
        help="Запустить только одну задачу (moves или mentions) вместо всех настроенных",
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

    tasks = _build_tasks(config, client)
    if not tasks:
        print(
            "Не настроено ни одной задачи: в файле правил пусты обе секции "
            "('rules' и 'mentions').",
            file=sys.stderr,
        )
        return 2

    selected = _select(tasks, args.only)
    if selected is None:
        return 2
    tasks = selected

    if args.check:
        return _check(client, config, tasks, logger)

    if args.once:
        failed = False
        for task in tasks:
            try:
                task.run_once()
            except ConfluenceError as exc:
                logger.error("Задача %r: ошибка: %s", task.name, exc)
                failed = True
        return 1 if failed else 0

    # Демон создаётся до старта health-сервера: так задачи попадают в /readyz
    # до того, как по нему начнут стучаться пробы.
    daemon = Daemon(tasks, client, health=health)
    start_health_server(health, config.health_port)

    try:
        daemon.run_forever()
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
