"""Подменные объекты для тестов цикла демона."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from confluence_label_bot.client import (
    ConfluenceConflict,
    ConfluenceError,
    Page,
    PageBody,
)
from confluence_label_bot.rules import GraphRule, MentionRule


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


class FakeConfluence:
    """Confluence в памяти: страницы с телами, учёт записей и сбоев.

    update_page_body поднимает версию и сохраняет новое тело, поэтому повторный
    проход читает уже записанное — именно это и проверяет идемпотентность.
    """

    def __init__(self, pages: dict[str, str], *, labels_hit: list[str] | None = None) -> None:
        # id → тело в storage format
        self.bodies = dict(pages)
        self.versions = {page_id: 1 for page_id in pages}
        self.titles = {page_id: f"Страница {page_id}" for page_id in pages}
        # Какие страницы отдавать как отобранные по лейблам.
        self._labels_hit = labels_hit if labels_hit is not None else list(pages)
        self.writes: list[tuple[str, str]] = []
        self.name_lookups: list[str] = []
        # Сколько ближайших записей должны упасть конфликтом версий.
        self.conflicts = 0
        # id страниц, чтение которых должно падать.
        self.broken: set[str] = set()
        # (id страницы, имя файла) → содержимое последней версии вложения.
        self.attachments: dict[tuple[str, str], bytes] = {}
        self.uploads: list[tuple[str, str, str]] = []

    def find_pages_with_labels_under(self, *, ancestor_id, labels, space_key=None):
        return [self._page(page_id) for page_id in self._labels_hit]

    def get_page_body(self, page_id: str) -> PageBody:
        if page_id in self.broken:
            raise ConfluenceError(f"страница {page_id} недоступна")
        return PageBody(page=self._page(page_id), storage=self.bodies[page_id])

    def update_page_body(self, page: Page, storage: str) -> None:
        if self.conflicts > 0:
            self.conflicts -= 1
            raise ConfluenceConflict("HTTP 409")
        self.bodies[page.id] = storage
        self.versions[page.id] = page.version + 1
        self.writes.append((page.id, storage))

    def put_attachment(self, page_id, filename, data, media_type, comment) -> str:
        outcome = "updated" if (page_id, filename) in self.attachments else "created"
        self.attachments[(page_id, filename)] = data
        self.uploads.append((page_id, filename, outcome))
        return outcome

    def user_display_name(self, key: str) -> str:
        self.name_lookups.append(key)
        return f"Имя-{key}"

    def _page(self, page_id: str) -> Page:
        return Page(
            id=page_id,
            title=self.titles[page_id],
            version=self.versions[page_id],
            space_key="DOCS",
            ancestor_ids=(),
        )


@dataclass
class FakeMentionConfig:
    """Минимум, который читает MentionCollectorBot."""

    mention_rules: tuple[MentionRule, ...]
    mentions_dry_run: bool = False
    mentions_cron: str = "0 * * * *"


@dataclass
class FakeGraphConfig:
    """Минимум, который читает ReleaseGraphBot."""

    graph_rules: tuple[GraphRule, ...]
    graph_dry_run: bool = False
    graph_cron: str = "0 * * * *"
