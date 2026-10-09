"""Задача графа: разбор правил, разметка страницы и поведение по cron."""

from __future__ import annotations

import logging

import pytest

from confluence_label_bot.release_graph import publishing
from confluence_label_bot.release_graph.parsing import parse_rows
from confluence_label_bot.release_graph.planning import build_plan
from confluence_label_bot.release_graph.task import ReleaseGraphBot
from confluence_label_bot.rules import GraphRule, RulesError, load_rule_set

from .fakes import FakeConfluence, FakeGraphConfig

PNG = "release-graph.png"


def _table(rows: list[tuple[str, str, str]]) -> str:
    """Таблица раскатки: (команда, релиз, зависимость)."""
    body = "".join(
        f"<tr><td>{team}</td><td>{release}</td><td>{depends}</td></tr>"
        for team, release, depends in rows
    )
    return (
        "<table><tbody>"
        "<tr><th>Команда</th><th>Релиз</th><th>Зависимость от релизов</th></tr>"
        f"{body}</tbody></table>"
    )


ROWS = [("Billing", "BILL-1", ""), ("Portal", "PORT-1", "BILL-1")]


def _page(rows=ROWS, marker: str = "<p>&lt;graph_placeholder&gt;</p>") -> str:
    return f"<p>Раскатка.</p>{_table(rows)}{marker}<p>Конец.</p>"


def _data(storage: str) -> dict:
    rows = parse_rows(storage)
    return publishing.plan_data(rows, build_plan(rows))


RULE = GraphRule(
    name="releases",
    root="555",
    labels=("release",),
    placeholder="graph_placeholder",
    attachment=PNG,
    width=None,
    space_key=None,
)


def _bot(pages: dict[str, str], *, dry_run: bool = False, rule: GraphRule = RULE):
    client = FakeConfluence(pages)
    config = FakeGraphConfig(graph_rules=(rule,), graph_dry_run=dry_run)
    return ReleaseGraphBot(config, client), client


def _write(tmp_path, text: str) -> str:
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


# ── правила ─────────────────────────────────────────────────────────────────
def test_правило_графа_с_умолчаниями(tmp_path):
    (rule,) = load_rule_set(_write(tmp_path, "graphs:\n  - root: 555\n    labels: release\n")).graphs

    assert rule.name == "graph-1"
    assert rule.root == "555"
    assert rule.labels == ("release",)
    assert rule.placeholder == "graph_placeholder"
    assert rule.attachment == "release-graph.png"
    assert rule.width is None


def test_только_секция_graphs_это_законный_конфиг(tmp_path):
    rule_set = load_rule_set(_write(tmp_path, "graphs:\n  - root: 1\n    labels: x\n"))
    assert rule_set
    assert not rule_set.moves and not rule_set.mentions


@pytest.mark.parametrize(
    "extra, message",
    [
        ("    attachment: graph.jpg\n", "расширение"),
        ("    width: -5\n", "width"),
        ("    colour: red\n", "неизвестные поля"),
    ],
)
def test_ошибки_в_правиле_графа(tmp_path, extra, message):
    text = "graphs:\n  - root: 1\n    labels: x\n" + extra
    with pytest.raises(RulesError, match=message):
        load_rule_set(_write(tmp_path, text))


def test_одно_вложение_в_одном_поддереве_дважды_это_ошибка(tmp_path):
    text = """
graphs:
  - {name: a, root: 1, labels: x, placeholder: one}
  - {name: b, root: 1, labels: y, placeholder: two}
"""
    with pytest.raises(RulesError, match="attachment"):
        load_rule_set(_write(tmp_path, text))


def test_разные_вложения_и_маркеры_в_одном_поддереве_разрешены(tmp_path):
    text = """
graphs:
  - {name: a, root: 1, labels: x}
  - {name: b, root: 1, labels: y, placeholder: other, attachment: other.svg}
"""
    assert len(load_rule_set(_write(tmp_path, text)).graphs) == 2


# ── разбор таблицы ──────────────────────────────────────────────────────────
HINT = "<ac:placeholder>Укажите релиз</ac:placeholder>"


def _commented_table(rows: list[tuple[str, str, str, str, str]]) -> str:
    """Таблица с номером и комментарием: (№, команда, релиз, зависимость, комментарий)."""
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return (
        "<table><tbody><tr><th>№</th><th>Команда</th><th>Релиз</th>"
        "<th>Зависимость от релизов</th><th>Комментарий</th></tr>"
        f"{body}</tbody></table>"
    )


@pytest.mark.parametrize(
    "blank",
    [
        ("2", "", "", "", ""),
        ("2", "&nbsp;", "<br/>", "", ""),
        ("2", "", "", "", "Ждём согласования"),
        ("2", f"<p>{HINT}</p>", HINT, HINT, "Ждём согласования"),
    ],
    ids=["пустая", "пробелы", "только-комментарий", "подсказки-и-комментарий"],
)
def test_пустая_строка_не_попадает_в_граф(blank):
    storage = _commented_table(
        [("1", "Billing", "BILL-1", "", ""), blank, ("3", "Portal", "PORT-1", "BILL-1", "")]
    )
    rows = parse_rows(storage)

    assert [row.num for row in rows] == ["1", "3"]
    plan = build_plan(rows)
    assert not plan.errors
    assert {num for wave in plan.waves for num in wave} == {"1", "3"}


def _rov_table(rov_cells: list[str]) -> str:
    body = "".join(
        f"<tr><td>T{i}</td><td>{rov}</td><td>REL-{i}</td><td></td><td>INST-{i}</td></tr>"
        for i, rov in enumerate(rov_cells, start=1)
    )
    return (
        "<table><tbody><tr><th>Команда</th><th>РоВ</th><th>Релиз</th>"
        "<th>Зависимость от релизов</th><th>Задача на установку</th></tr>"
        f"{body}</tbody></table>"
    )


def test_ров_читается_ключами_либо_текстом_и_попадает_в_json():
    jira = (
        '<ac:structured-macro ac:name="jira">'
        '<ac:parameter ac:name="key">ROV-7</ac:parameter></ac:structured-macro>'
    )
    rows = parse_rows(_rov_table([jira, "№ 15 от 01.10", ""]))

    assert [row.rov for row in rows] == [["ROV-7"], ["№ 15 от 01.10"], []]
    assert rows[0].install_tasks == ["INST-1"]
    data = publishing.plan_data(rows, build_plan(rows))
    assert [row["rov"] for row in data["rows"]] == [["ROV-7"], ["№ 15 от 01.10"], []]


def test_ров_выводится_на_картинке():
    from confluence_label_bot.release_graph.render import render_image

    rows = parse_rows(_rov_table(["ROV-7", ""]))
    svg = render_image(rows, build_plan(rows), fmt="svg").decode("utf-8")

    assert "РоВ: ROV-7" in svg
    assert svg.count("РоВ:") == 1  # у строки без РоВ подписи нет


def test_подсказка_шаблона_не_считается_значением():
    storage = _commented_table([("1", f"<p>{HINT}</p>", "BILL-1", HINT, "")])
    (row,) = parse_rows(storage)

    assert row.team == ""
    assert row.releases == ["BILL-1"]
    assert row.unfilled == ["team", "depends_on"]


# ── разметка ────────────────────────────────────────────────────────────────
def test_json_встаёт_за_абзацем_с_картинкой():
    storage = _page()
    insertion = publishing.insert_image(storage, PNG, "graph_placeholder")
    new, changed = publishing.insert_json(insertion.storage, PNG, _data(storage))

    assert insertion.kind == publishing.PLACEHOLDER
    assert changed
    image = publishing.image_storage(PNG)
    assert f"<p>{image}</p><ac:structured-macro" in new
    assert new.endswith("</ac:structured-macro><p>Конец.</p>")


def test_json_со_страницы_разбирается_обратно():
    storage = _page(rows=[("A > B", "BILL-1", "")])
    data = _data(storage)
    insertion = publishing.insert_image(storage, PNG, "graph_placeholder")
    new, _ = publishing.insert_json(insertion.storage, PNG, data)

    # «>» из названия команды экранирован и не рвёт ни CDATA, ни макрос.
    assert "A > B" not in new.split("<ac:structured-macro")[1]
    _, parsed = publishing.find_json(new, "release-graph.json")
    assert parsed == data


def test_повторная_разметка_ничего_не_меняет():
    storage = _page()
    data = _data(storage)
    first, _ = publishing.insert_json(
        publishing.insert_image(storage, PNG, "graph_placeholder").storage, PNG, data
    )
    again = publishing.insert_image(first, PNG, "graph_placeholder")
    second, changed = publishing.insert_json(again.storage, PNG, data)

    assert again.kind == publishing.EXISTING
    assert not changed
    assert second == first


def test_атрибуты_от_confluence_не_дают_ложной_правки():
    storage = _page()
    data = _data(storage)
    first, _ = publishing.insert_json(
        publishing.insert_image(storage, PNG, "graph_placeholder").storage, PNG, data
    )
    saved = first.replace(
        '<ac:structured-macro ac:name="code">',
        '<ac:structured-macro ac:name="code" ac:schema-version="1" ac:macro-id="x1">',
    )
    new, changed = publishing.insert_json(saved, PNG, data)
    assert not changed
    assert new == saved


def test_json_обновляется_на_месте_а_не_дублируется():
    storage = _page()
    first, _ = publishing.insert_json(
        publishing.insert_image(storage, PNG, "graph_placeholder").storage, PNG, _data(storage)
    )
    other = _data(_page(rows=ROWS + [("Ledger", "LEDG-1", "PORT-1")]))
    new, changed = publishing.insert_json(first, PNG, other)

    assert changed
    assert new.count('ac:name="code"') == 1
    assert publishing.find_json(new, "release-graph.json")[1] == other


def test_чужой_макрос_code_не_трогается():
    foreign = (
        '<ac:structured-macro ac:name="code"><ac:parameter ac:name="title">other</ac:parameter>'
        "<ac:plain-text-body><![CDATA[{}]]></ac:plain-text-body></ac:structured-macro>"
    )
    storage = _page(marker=f"{foreign}<p>&lt;graph_placeholder&gt;</p>")
    new, _ = publishing.insert_json(
        publishing.insert_image(storage, PNG, "graph_placeholder").storage, PNG, _data(storage)
    )
    assert foreign in new
    assert new.count('ac:name="code"') == 2


# ── задача ──────────────────────────────────────────────────────────────────
def test_первый_проход_заливает_картинку_и_пишет_json():
    bot, client = _bot({"1": _page()})

    assert bot.run_once() == 1
    assert client.uploads == [("1", PNG, "created")]
    assert client.attachments[("1", PNG)].startswith(b"\x89PNG")
    body = client.bodies["1"]
    assert "graph_placeholder" not in body
    assert 'ri:filename="release-graph.png"' in body
    assert publishing.find_json(body, "release-graph.json")[1] == _data(_page())


def test_повторный_проход_не_трогает_ни_страницу_ни_вложение():
    bot, client = _bot({"1": _page()})
    bot.run_once()

    assert bot.run_once() == 0
    assert len(client.writes) == 1
    assert len(client.uploads) == 1


def test_изменённая_таблица_перерисовывает_граф():
    bot, client = _bot({"1": _page()})
    bot.run_once()
    client.bodies["1"] = client.bodies["1"].replace(
        _table(ROWS), _table(ROWS + [("Ledger", "LEDG-1", "PORT-1")])
    )

    assert bot.run_once() == 1
    assert client.uploads[-1] == ("1", PNG, "updated")
    assert len(client.writes) == 2


def test_svg_по_расширению_вложения():
    rule = GraphRule(**{**RULE.__dict__, "attachment": "graph.svg"})
    bot, client = _bot({"1": _page()}, rule=rule)
    bot.run_once()

    assert client.attachments[("1", "graph.svg")].startswith(b"<?xml")
    assert publishing.find_json(client.bodies["1"], "graph.json")[1] is not None


def test_dry_run_ничего_не_меняет(caplog):
    bot, client = _bot({"1": _page()}, dry_run=True)
    with caplog.at_level(logging.INFO):
        assert bot.run_once() == 0

    assert not client.writes and not client.uploads
    assert "[DRY_RUN]" in caplog.text


def test_страница_без_таблицы_пропускается(caplog):
    bot, client = _bot({"1": "<p>&lt;graph_placeholder&gt;</p>", "2": _page()})
    with caplog.at_level(logging.WARNING):
        assert bot.run_once() == 1

    assert "1" not in {page for page, _ in client.writes}
    assert "No rollout table found" in caplog.text


def test_страница_без_маркера_и_картинки_пропускается(caplog):
    bot, client = _bot({"1": _page(marker="")})
    with caplog.at_level(logging.WARNING):
        assert bot.run_once() == 0

    assert not client.writes and not client.uploads
    assert "graph_placeholder" in caplog.text


def test_конфликт_версий_повторяется_без_второй_заливки():
    bot, client = _bot({"1": _page()})
    client.conflicts = 1

    assert bot.run_once() == 1
    assert len(client.writes) == 1
    assert len(client.uploads) == 1


def test_битая_страница_не_срывает_проход():
    bot, client = _bot({"1": _page(), "2": _page()})
    client.broken.add("1")

    assert bot.run_once() == 1
    assert [page for page, _ in client.writes] == ["2"]
