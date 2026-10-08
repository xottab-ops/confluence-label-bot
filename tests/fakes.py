"""Подменные объекты для тестов цикла демона."""

from __future__ import annotations

from datetime import datetime, timedelta


class FakeClock:
    """Часы, которые двигает сам тест: sleep не спит, а переводит время вперёд.

    Благодаря этому цикл демона прокручивается мгновенно, а длительности сна
    остаются доступны для проверок в self.slept.
    """

    def __init__(self, start: datetime) -> None:
        self.moment = start
        self.slept: list[float] = []

    def now(self) -> datetime:
        return self.moment

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.moment += timedelta(seconds=seconds)


class FakeTask:
    """Задача, которая только считает свои запуски."""

    def __init__(self, name: str, cron: str, *, fails: bool = False) -> None:
        self.name = name
        self.cron = cron
        self.fails = fails
        self.runs = 0

    def run_once(self) -> int:
        self.runs += 1
        if self.fails:
            raise RuntimeError("задача сломалась")
        return 0

    def describe(self) -> list[str]:
        return [f"{self.name}: подменная задача"]


class FakeHealth:
    """Записывает обращения демона к health вместо настоящего состояния."""

    def __init__(self) -> None:
        self.beats = 0
        self.registered: list[str] = []
        self.runs: list[tuple[str, bool, str | None]] = []
        self.ready: bool | None = None

    def beat(self) -> None:
        self.beats += 1

    def register_task(self, task: str) -> None:
        self.registered.append(task)

    def record_run(
        self, task: str, ok: bool, *, at: datetime, error: str | None = None
    ) -> None:
        self.runs.append((task, ok, error))

    def set_ready(self, value: bool) -> None:
        self.ready = value


class FakeClient:
    """Клиент, у которого демону нужен только get_current_user."""

    def __init__(self, user: str = "bot (Бот)", error: Exception | None = None) -> None:
        self._user = user
        self._error = error

    def get_current_user(self) -> str:
        if self._error is not None:
            raise self._error
        return self._user
