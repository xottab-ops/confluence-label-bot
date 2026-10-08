"""Состояние здоровья: статус по задачам, liveness и readiness."""

from __future__ import annotations

from datetime import datetime

from confluence_label_bot.health import HealthState


class Monotonic:
    """Подменные монотонные часы: heartbeat считается не по настенному времени."""

    def __init__(self) -> None:
        self.value = 1000.0

    def __call__(self) -> float:
        return self.value


def test_задача_видна_до_первого_прогона():
    state = HealthState(300.0)
    state.register_task("mentions")

    assert state.tasks == {
        "mentions": {"last_run_ok": None, "last_run_at": None, "last_error": None}
    }


def test_повторная_регистрация_не_затирает_результат():
    state = HealthState(300.0)
    state.register_task("moves")
    state.record_run("moves", True, at=datetime(2026, 10, 8, 10, 5))

    state.register_task("moves")

    assert state.tasks["moves"]["last_run_ok"] is True


def test_результаты_задач_не_смешиваются():
    state = HealthState(300.0)
    state.record_run("moves", True, at=datetime(2026, 10, 8, 10, 5))
    state.record_run("mentions", False, at=datetime(2026, 10, 8, 11, 0), error="HTTP 500")

    assert state.tasks == {
        "moves": {
            "last_run_ok": True,
            "last_run_at": "2026-10-08T10:05:00",
            "last_error": None,
        },
        "mentions": {
            "last_run_ok": False,
            "last_run_at": "2026-10-08T11:00:00",
            "last_error": "HTTP 500",
        },
    }


def test_упавшая_задача_не_роняет_readiness():
    """Иначе сбой второстепенной задачи выводил бы под из балансировки."""
    state = HealthState(300.0)
    state.set_ready(True)

    state.record_run("mentions", False, at=datetime(2026, 10, 8, 11, 0), error="HTTP 500")

    assert state.ready is True


def test_liveness_живёт_пока_heartbeat_свежий():
    clock = Monotonic()
    state = HealthState(300.0, monotonic=clock)

    clock.value += 299.0
    assert state.alive is True

    clock.value += 2.0
    assert state.alive is False


def test_heartbeat_обнуляет_возраст():
    clock = Monotonic()
    state = HealthState(300.0, monotonic=clock)

    clock.value += 400.0
    assert state.alive is False

    state.beat()
    assert state.alive is True
    assert state.heartbeat_age == 0.0
