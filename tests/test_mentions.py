"""Задача сбора упоминаний: когда пишет, когда молчит и как держит сбои."""

from __future__ import annotations

import logging

import pytest

from confluence_label_bot.mentions import MentionCollectorBot
from confluence_label_bot.rules import MentionRule

from .fakes import FakeConfluence, FakeMentionConfig


def _mention(key: str) -> str:
    return f'<ac:link><ri:user ri:userkey="{key}" /></ac:link>'


def _page(responsible: list[str], placeholder: str = "") -> str:
    rows = "".join(
        f"<tr><td>Задача {index}</td><td>{_mention(key)}</td></tr>"
        for index, key in enumerate(responsible)
    )
    return (
        "<p>Релиз.</p>"
        "<table><tbody>"
        "<tr><th>Задача</th><th>Ответственный</th></tr>"
        f"{rows}"
        "</tbody></table>"
        "<table><tbody>"
        f"<tr><th>Участники</th><td>{placeholder}</td></tr>"
        "</tbody></table>"
    )


RULE = MentionRule(
    name="release-owners",
    root="555",
    labels=("release",),
    columns=("Ответственный",),
    placeholder="Участники",
    space_key=None,
)


def _bot(pages: dict[str, str], *, dry_run: bool = False, **kwargs):
    client = FakeConfluence(pages, **kwargs)
    config = FakeMentionConfig(mention_rules=(RULE,), mentions_dry_run=dry_run)
    return MentionCollectorBot(config, client), client


# ── основной путь ───────────────────────────────────────────────────────────
def test_упоминания_попадают_в_плейсхолдер():
    bot, client = _bot({"1": _page(["aaa111", "bbb222"])})

    assert bot.run_once() == 1
    assert client.bodies["1"].count(_mention("aaa111")) == 2  # в таблице и в приёмнике
    assert "<td><p>" + _mention("aaa111") + ", " + _mention("bbb222") + "</p></td>" in (
        client.bodies["1"]
    )


def test_таблица_источник_не_тронута():
    source = "<tr><th>Задача</th><th>Ответственный</th></tr>"
    bot, client = _bot({"1": _page(["aaa111"])})

    bot.run_once()

    assert source in client.bodies["1"]
    assert "<p>Релиз.</p>" in client.bodies["1"]


def test_второй_проход_не_пишет():
    """Главная защита: бот ходит по cron, и лишняя запись — это версия и письма."""
    bot, client = _bot({"1": _page(["aaa111", "bbb222"])})

    assert bot.run_once() == 1
    assert bot.run_once() == 0
    assert len(client.writes) == 1


def test_изменение_состава_перезаписывает_плейсхолдер():
    bot, client = _bot({"1": _page(["aaa111"])})
    bot.run_once()

    # В таблице появился второй человек.
    client.bodies["1"] = _page(
        ["aaa111", "bbb222"], placeholder="<p>" + _mention("aaa111") + "</p>"
    )
    client.versions["1"] = 2

    assert bot.run_once() == 1
    assert _mention("bbb222") in client.bodies["1"].split("Участники")[1]


def test_опустевший_столбец_очищает_плейсхолдер():
    """Иначе выбывший из таблицы остался бы ответственным навсегда."""
    bot, client = _bot({"1": _page([], placeholder="<p>" + _mention("aaa111") + "</p>")})

    assert bot.run_once() == 1
    assert "<tr><th>Участники</th><td></td></tr>" in client.bodies["1"]


def test_несколько_страниц_обрабатываются_независимо():
    bot, client = _bot({"1": _page(["aaa111"]), "2": _page(["bbb222"])})

    assert bot.run_once() == 2
    assert {page_id for page_id, _ in client.writes} == {"1", "2"}


# ── отказ от записи ─────────────────────────────────────────────────────────
def test_без_плейсхолдера_страница_пропускается(caplog):
    page = "<table><tbody><tr><th>Ответственный</th></tr>" f"<tr><td>{_mention('aaa111')}</td></tr></tbody></table>"
    bot, client = _bot({"1": page})

    with caplog.at_level(logging.WARNING):
        assert bot.run_once() == 0

    assert client.writes == []
    assert "нет строки-плейсхолдера" in caplog.text


def test_без_столбца_страница_пропускается(caplog):
    page = "<table><tbody><tr><th>Участники</th><td></td></tr></tbody></table>"
    bot, client = _bot({"1": page})

    with caplog.at_level(logging.WARNING):
        assert bot.run_once() == 0

    assert client.writes == []
    assert "нет столбца" in caplog.text


def test_пустой_столбец_и_пустой_плейсхолдер_записи_не_требуют():
    """Столбец на месте, но никого не отметили — менять нечего."""
    bot, client = _bot({"1": _page([])})

    assert bot.run_once() == 0
    assert client.writes == []


def test_несколько_плейсхолдеров_берётся_первый(caplog):
    page = (
        "<table><tbody>"
        "<tr><th>Ответственный</th></tr>"
        f"<tr><td>{_mention('aaa111')}</td></tr>"
        "</tbody></table>"
        "<table><tbody>"
        "<tr><th>Участники</th><td>первый</td></tr>"
        "<tr><th>Участники</th><td>второй</td></tr>"
        "</tbody></table>"
    )
    bot, client = _bot({"1": page})

    with caplog.at_level(logging.WARNING):
        assert bot.run_once() == 1

    assert "несколько" in caplog.text
    assert "второй" in client.bodies["1"]


def test_пробный_прогон_ничего_не_пишет(caplog):
    bot, client = _bot({"1": _page(["aaa111"])}, dry_run=True)

    with caplog.at_level(logging.INFO):
        assert bot.run_once() == 0

    assert client.writes == []
    assert "[DRY_RUN]" in caplog.text
    # В логе должны быть имена, а не опознаватели.
    assert "Имя-aaa111" in caplog.text


# ── сбои ────────────────────────────────────────────────────────────────────
def test_конфликт_версий_приводит_к_перечитыванию_и_повтору():
    bot, client = _bot({"1": _page(["aaa111"])})
    client.conflicts = 1

    assert bot.run_once() == 1
    assert len(client.writes) == 1


def test_повторный_конфликт_не_валит_проход(caplog):
    bot, client = _bot({"1": _page(["aaa111"])})
    client.conflicts = 5

    with caplog.at_level(logging.ERROR):
        assert bot.run_once() == 0

    assert client.writes == []
    assert "конфликт версий и после повтора" in caplog.text


def test_битая_страница_не_срывает_остальные(caplog):
    bot, client = _bot({"1": _page(["aaa111"]), "2": _page(["bbb222"])})
    client.broken = {"1"}

    with caplog.at_level(logging.ERROR):
        assert bot.run_once() == 1

    assert [page_id for page_id, _ in client.writes] == ["2"]
    assert "ошибка, пропуск" in caplog.text


# ── протокол задачи ─────────────────────────────────────────────────────────
def test_задача_подходит_под_протокол_демона():
    bot, _ = _bot({})

    assert bot.name == "mentions"
    assert bot.cron == "0 * * * *"
    assert bot.describe() == [
        "release-owners: 555 (release) столбец 'Ответственный' "
        "--> плейсхолдер 'Участники'"
    ]


@pytest.mark.parametrize("column", ["Ответственный", "  ОТВЕТСТВЕННЫЙ  "])
def test_имя_столбца_из_конфига_сверяется_нестрого(column):
    client = FakeConfluence({"1": _page(["aaa111"])})
    rule = MentionRule(
        name="r",
        root="555",
        labels=("release",),
        columns=(column,),
        placeholder="участники",
        space_key=None,
    )
    bot = MentionCollectorBot(FakeMentionConfig(mention_rules=(rule,)), client)

    assert bot.run_once() == 1
