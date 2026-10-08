"""Цикл демона: ближайший срок, совпадение сроков, изоляция сбоев."""

from __future__ import annotations

from datetime import datetime

import pytest

from confluence_label_bot.daemon import Daemon

from .fakes import FakeClient, FakeClock, FakeHealth, FakeTask

MOVES_CRON = "*/5 * * * *"
MENTIONS_CRON = "0 * * * *"


def _daemon(start: datetime, *tasks, tick: float = 5.0):
    clock = FakeClock(start)
    health = FakeHealth()
    daemon = Daemon(tasks, FakeClient(), health, tick=tick, clock=clock)
    return daemon, clock, health


def test_демону_нужна_хотя_бы_одна_задача():
    with pytest.raises(ValueError, match="хотя бы одна задача"):
        Daemon([], FakeClient())


def test_задачи_попадают_в_health_сразу_при_создании():
    """Иначе задачу с редким расписанием не видно в /readyz до первого прогона."""
    _, _, health = _daemon(
        datetime(2026, 10, 8, 10, 2),
        FakeTask("moves", MOVES_CRON),
        FakeTask("mentions", MENTIONS_CRON),
    )

    assert health.registered == ["moves", "mentions"]


def test_ближайший_срок_берётся_по_всем_задачам():
    moves = FakeTask("moves", MOVES_CRON)
    mentions = FakeTask("mentions", MENTIONS_CRON)
    daemon, _, _ = _daemon(datetime(2026, 10, 8, 10, 2), moves, mentions)

    deadline, due = daemon.next_due(datetime(2026, 10, 8, 10, 2))

    assert deadline == datetime(2026, 10, 8, 10, 5)
    assert due == [moves]


def test_совпавшие_сроки_отдаются_в_порядке_регистрации():
    """В начале часа «*/5» и «0 * * * *» сходятся, и переносы должны идти первыми."""
    moves = FakeTask("moves", MOVES_CRON)
    mentions = FakeTask("mentions", MENTIONS_CRON)
    daemon, _, _ = _daemon(datetime(2026, 10, 8, 10, 57, 30), moves, mentions)

    deadline, due = daemon.next_due(datetime(2026, 10, 8, 10, 57, 30))

    assert deadline == datetime(2026, 10, 8, 11, 0)
    assert due == [moves, mentions]


def test_проход_запускает_только_задачу_с_подошедшим_сроком():
    moves = FakeTask("moves", MOVES_CRON)
    mentions = FakeTask("mentions", MENTIONS_CRON)
    daemon, clock, health = _daemon(datetime(2026, 10, 8, 10, 2), moves, mentions)

    daemon.run_cycle()

    assert (moves.runs, mentions.runs) == (1, 0)
    assert clock.moment == datetime(2026, 10, 8, 10, 5)
    assert health.runs == [("moves", True, None)]


def test_проход_запускает_обе_задачи_при_совпадении_сроков():
    moves = FakeTask("moves", MOVES_CRON)
    mentions = FakeTask("mentions", MENTIONS_CRON)
    daemon, _, health = _daemon(datetime(2026, 10, 8, 10, 57, 30), moves, mentions)

    daemon.run_cycle()

    assert (moves.runs, mentions.runs) == (1, 1)
    assert [name for name, _, _ in health.runs] == ["moves", "mentions"]


def test_сбой_одной_задачи_не_мешает_другой():
    moves = FakeTask("moves", MOVES_CRON, fails=True)
    mentions = FakeTask("mentions", MENTIONS_CRON)
    daemon, _, health = _daemon(datetime(2026, 10, 8, 10, 57, 30), moves, mentions)

    daemon.run_cycle()

    assert mentions.runs == 1
    assert health.runs == [
        ("moves", False, "задача сломалась"),
        ("mentions", True, None),
    ]


def test_сон_разбит_на_тики_и_на_каждом_отмечается_heartbeat():
    """Цельный сон до редкого срока протухил бы heartbeat, и k8s убил бы живой под."""
    daemon, clock, health = _daemon(
        datetime(2026, 10, 8, 10, 0), FakeTask("moves", "0 12 * * *"), tick=60.0
    )

    daemon.run_cycle()

    # Два часа сна тиками по минуте.
    assert clock.slept == [60.0] * 120
    assert health.beats == 120
    assert clock.moment == datetime(2026, 10, 8, 12, 0)


def test_последний_тик_не_перелетает_за_срок():
    daemon, clock, _ = _daemon(
        datetime(2026, 10, 8, 10, 4, 30), FakeTask("moves", MOVES_CRON), tick=60.0
    )

    daemon.run_cycle()

    assert clock.slept == [30.0]
    assert clock.moment == datetime(2026, 10, 8, 10, 5)


def test_срок_считается_от_текущего_момента_а_не_копится():
    """Если проход затянулся дольше промежутка, пропущенные сроки не накапливаются."""
    moves = FakeTask("moves", MOVES_CRON)
    daemon, _, _ = _daemon(datetime(2026, 10, 8, 10, 2), moves)

    # Проход «занял» 12 минут: прошли сроки 10:05 и 10:10.
    deadline, _ = daemon.next_due(datetime(2026, 10, 8, 10, 14))

    assert deadline == datetime(2026, 10, 8, 10, 15)
