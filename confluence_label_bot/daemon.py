"""Цикл демона: несколько задач, у каждой своё cron-расписание.

Задачи живут в одном процессе и в одном потоке, хотя расписания у них разные.
Это не упрощение ради простоты, а требование: на `_last_request_at` внутри
ConfluenceClient держится вся защита от HTTP 429, а лимит Confluence —
глобальный на пользователя. Отдельные потоки поверх одного клиента ломают
троттлинг (стреляют одновременно), отдельные клиенты ломают его сильнее: пауза
становится локальной, и фактическая частота обращений оказывается кратно выше
заявленной в CONFLUENCE_QUERY_DELAY.

Поэтому цикл считает ближайший срок по всем задачам, спит до него и выполняет
все задачи, чей срок подошёл, — в порядке регистрации.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

from croniter import croniter

from .client import ConfluenceClient, ConfluenceError
from .health import HealthState

logger = logging.getLogger(__name__)


class ScheduledTask(Protocol):
    """Задача демона: как её звать, когда запускать и что делает один проход."""

    name: str
    cron: str

    def run_once(self) -> int:
        """Один проход. Возвращает количество изменённых сущностей."""

    def describe(self) -> list[str]:
        """Строки для стартового лога — по одной на правило задачи."""


class Clock:
    """Время и сон одной сущностью, чтобы цикл можно было прокрутить в тестах.

    В бою это просто datetime.now() и time.sleep(); в тестах — подменный класс,
    двигающий время вперёд на запрошенную длительность вместо настоящего сна.
    """

    def now(self) -> datetime:
        return datetime.now()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _humanize(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total} с"
    minutes, seconds = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} мин {seconds} с"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин"


class Daemon:
    def __init__(
        self,
        tasks: Sequence[ScheduledTask],
        client: ConfluenceClient,
        health: HealthState | None = None,
        *,
        tick: float = 5.0,
        clock: Clock | None = None,
    ) -> None:
        if not tasks:
            raise ValueError("Демону нужна хотя бы одна задача")
        self._tasks = tuple(tasks)
        self._client = client
        self._health = health
        self._tick = tick
        self._clock = clock or Clock()

        # Задачи заводятся в health до старта HTTP-сервера: тогда в /readyz они
        # видны с самого начала, а словарь статусов дальше не перестраивается
        # под читающим потоком — см. комментарий в HealthState.
        if health is not None:
            for task in self._tasks:
                health.register_task(task.name)

    def next_due(self, now: datetime) -> tuple[datetime, list[ScheduledTask]]:
        """Ближайший срок по всем задачам и все задачи с этим сроком.

        Срок считается от now, а не от прошлого срабатывания: если проход
        затянулся дольше промежутка между запусками, пропущенные сроки не
        копятся — просто идём к ближайшему будущему.
        """
        schedule = [(croniter(task.cron, now).get_next(datetime), task) for task in self._tasks]
        earliest = min(deadline for deadline, _ in schedule)
        # Срок у cron дискретен до минуты, поэтому совпадения — обычное дело
        # («*/5 * * * *» и «0 * * * *» сходятся в начале часа). Порядок берётся
        # от регистрации: переносы раньше задач, правящих тело страниц.
        return earliest, [task for deadline, task in schedule if deadline == earliest]

    def run_cycle(self) -> None:
        """Дождаться ближайшего срока и выполнить все задачи с этим сроком."""
        now = self._clock.now()
        deadline, due = self.next_due(now)
        logger.info(
            "Следующий запуск: %s (через %s) — %s",
            deadline.strftime("%Y-%m-%d %H:%M:%S"),
            _humanize(deadline - now),
            ", ".join(task.name for task in due),
        )
        self._sleep_until(deadline)
        for task in due:
            self._run(task)

    def run_forever(self) -> None:
        self._log_startup()

        # Стартовая проверка связи → readiness. До первого успешного контакта с
        # Confluence под считается «не готов» (readiness=false), но «живым»
        # (liveness=true) — k8s не гонит на него трафик, но и не рестартит.
        self._check_connectivity()

        while True:
            self.run_cycle()

    # ── внутреннее ──────────────────────────────────────────────────────────
    def _log_startup(self) -> None:
        logger.info("Демон запущен. Задач: %d", len(self._tasks))
        for task in self._tasks:
            logger.info("  %s — расписание %r", task.name, task.cron)
            for line in task.describe():
                logger.info("      %s", line)

    def _sleep_until(self, deadline: datetime) -> None:
        """Спать до deadline короткими тиками, обновляя heartbeat.

        Цельный sleep на весь промежуток между запусками при редком cron (напр.
        раз в сутки) протухил бы heartbeat, и liveness-проба убила бы живой под.
        Поэтому спим по чуть-чуть и на каждом тике отмечаемся живыми.
        """
        while True:
            remaining = (deadline - self._clock.now()).total_seconds()
            if remaining <= 0:
                return
            if self._health is not None:
                self._health.beat()
            self._clock.sleep(min(self._tick, remaining))

    def _run(self, task: ScheduledTask) -> None:
        try:
            task.run_once()
        except Exception as exc:  # noqa: BLE001 — сбой одной задачи не валит демон
            logger.exception("Задача %r: непредвиденная ошибка", task.name)
            self._record(task, False, str(exc))
        else:
            self._record(task, True)

    def _record(self, task: ScheduledTask, ok: bool, error: str | None = None) -> None:
        if self._health is not None:
            self._health.record_run(task.name, ok, at=self._clock.now(), error=error)

    def _check_connectivity(self) -> None:
        """Проверить связь с Confluence и выставить readiness."""
        if self._health is None:
            return
        try:
            user = self._client.get_current_user()
        except ConfluenceError as exc:
            logger.error(
                "Стартовая проверка связи с Confluence не прошла (readiness=false): %s",
                exc,
            )
            return
        logger.info("Связь с Confluence есть, работаем под пользователем: %s", user)
        self._health.set_ready(True)
