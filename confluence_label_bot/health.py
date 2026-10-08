"""Лёгкий HTTP-сервер здоровья для проб Kubernetes (liveness/readiness).

Приложение — фоновый демон без собственного веб-интерфейса, поэтому статус для
Kubernetes отдаём отдельным сервером на stdlib (без новых зависимостей),
поднятым в потоке-демоне.

- /healthz (liveness): жив ли процесс и не завис ли главный цикл. Цикл бота
  постоянно обновляет heartbeat — и во время работы (на каждом HTTP-запросе к
  Confluence), и во время сна между запусками по cron (сон разбит на короткие
  тики). Поэтому «свежесть» heartbeat означает «главный поток отвечает», а не
  «давно ли был прогон» — порог liveness не зависит от интервала cron. Иначе
  при редком расписании (напр. «0 6 * * *») перезапуск днём привёл бы к тому,
  что совершенно живой под спит до утра, а liveness-проба его убивает по кругу.
- /readyz (readiness): прошла ли стартовая проверка связи с Confluence. Там же —
  результат последнего прогона каждой задачи, но уже справочно: упавшая задача
  readiness не роняет, иначе сбой второстепенной задачи выводил бы из
  балансировки под, который исправно делает всё остальное.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskStatus:
    """Результат последнего прогона одной задачи."""

    last_run_ok: bool | None = None
    last_run_at: str | None = None
    last_error: str | None = None


class HealthState:
    """Разделяемое между потоками состояние здоровья.

    Обновляется главным потоком бота (beat/set_ready/record_run), читается
    потоком HTTP-сервера. Поля — простые атомарные присваивания, блокировка не
    нужна. Это верно и для словаря задач: все ключи заводятся через
    register_task до старта HTTP-сервера, а дальше только подменяется значение
    по существующему ключу — словарь не перестраивается под читателем.
    """

    def __init__(
        self, liveness_timeout: float, *, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._liveness_timeout = liveness_timeout
        self._monotonic = monotonic
        self._heartbeat = monotonic()
        self.ready = False
        self._tasks: dict[str, TaskStatus] = {}

    def beat(self) -> None:
        """Отметить, что главный поток жив (вызывается часто)."""
        self._heartbeat = self._monotonic()

    def set_ready(self, value: bool) -> None:
        self.ready = value

    def register_task(self, task: str) -> None:
        """Завести задачу в выдаче /readyz до её первого прогона.

        Иначе до первого срабатывания редкого расписания задачи не видно
        вообще, и «не запускалась» неотличимо от «не настроена».
        """
        self._tasks.setdefault(task, TaskStatus())

    def record_run(
        self, task: str, ok: bool, *, at: datetime, error: str | None = None
    ) -> None:
        self._tasks[task] = TaskStatus(
            last_run_ok=ok,
            last_run_at=at.isoformat(timespec="seconds"),
            last_error=error,
        )

    @property
    def tasks(self) -> dict[str, dict[str, object]]:
        return {
            name: {
                "last_run_ok": status.last_run_ok,
                "last_run_at": status.last_run_at,
                "last_error": status.last_error,
            }
            for name, status in self._tasks.items()
        }

    @property
    def heartbeat_age(self) -> float:
        return self._monotonic() - self._heartbeat

    @property
    def alive(self) -> bool:
        return self.heartbeat_age < self._liveness_timeout


def _make_handler(state: HealthState) -> type[BaseHTTPRequestHandler]:
    class _HealthHandler(BaseHTTPRequestHandler):
        # Стандартный access-log http.server пишет в stderr — глушим, чтобы
        # частые пробы k8s не засоряли вывод.
        def log_message(self, *args: object) -> None:  # noqa: D401
            return

        def _send(self, code: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 — имя задаёт BaseHTTPRequestHandler
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/healthz":
                alive = state.alive
                self._send(
                    200 if alive else 503,
                    {
                        "status": "ok" if alive else "stuck",
                        "heartbeat_age": round(state.heartbeat_age, 1),
                    },
                )
            elif path == "/readyz":
                ready = state.ready
                self._send(
                    200 if ready else 503,
                    {"ready": ready, "tasks": state.tasks},
                )
            else:
                self._send(404, {"error": "not found"})

    return _HealthHandler


def start_health_server(
    state: HealthState, port: int, host: str = "0.0.0.0"
) -> ThreadingHTTPServer:
    """Поднять health-сервер в потоке-демоне и вернуть его.

    Поток — daemon, поэтому не мешает процессу завершиться. Возвращаем сервер,
    чтобы вызывающий при желании мог сделать shutdown().
    """
    server = ThreadingHTTPServer((host, port), _make_handler(state))
    thread = threading.Thread(target=server.serve_forever, name="health", daemon=True)
    thread.start()
    logger.info("Health-сервер слушает http://%s:%d (/healthz, /readyz)", host, port)
    return server