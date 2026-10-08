"""Разбор и правка storage format: смещения, столбцы, упоминания."""

from __future__ import annotations

import pytest

from confluence_label_bot.storage import (
    collect_mentions,
    find_placeholders,
    has_columns,
    normalize,
    parse_tables,
    render_mentions,
    same_mentions,
    splice,
)

# Приватные детали токенайзера: инвариант покрытия документа проверяется
# напрямую, потому что на нём держится вся правка срезами.
from confluence_label_bot.storage import _token_end, _tokenize


def _mention(key: str) -> str:
    return f'<ac:link><ri:user ri:userkey="{key}" /></ac:link>'


# Тело страницы в том виде, в каком его отдаёт Confluence: colgroup, tbody,
# неразрывные пробелы, самозакрывающиеся теги.
PAGE = f"""<p>Страница релиза.</p>
<table>
  <colgroup><col /><col /><col /></colgroup>
  <tbody>
    <tr>
      <th>Задача</th>
      <th>Ответственный&nbsp;</th>
      <th>Статус</th>
    </tr>
    <tr>
      <td>Собрать сборку</td>
      <td>{_mention("aaa111")}&nbsp;</td>
      <td>В работе</td>
    </tr>
    <tr>
      <td>Прогнать тесты</td>
      <td>{_mention("bbb222")}, {_mention("aaa111")}</td>
      <td>Готово</td>
    </tr>
  </tbody>
</table>
<table>
  <tbody>
    <tr><th>Участники</th><td></td></tr>
    <tr><th>Статус</th><td>В работе</td></tr>
  </tbody>
</table>"""


# ── фундамент: смещения ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "storage",
    [
        PAGE,
        "<p>одна строка без переводов</p>",
        "<p>&nbsp;&amp;&#1057;</p>",
        '<ac:structured-macro ac:name="code"><ac:plain-text-body><![CDATA[a < b]]>'
        "</ac:plain-text-body></ac:structured-macro>",
        "<p><!-- комментарий --></p>",
        "",
    ],
)
def test_конструкции_покрывают_документ_без_щелей(storage):
    """Срезы по смещениям конструкций должны склеиваться в исходник.

    Это тот самый инвариант, на котором держится правка срезом: стоит
    смещениям разъехаться хоть на символ, и бот начнёт портить страницы.
    """
    tokens = _tokenize(storage)
    pieces = [
        storage[token.start : _token_end(tokens, index, len(storage))]
        for index, token in enumerate(tokens)
    ]

    assert "".join(pieces) == storage
    assert tokens == [] or tokens[0].start == 0


def test_нормализация_схлопывает_неразрывные_пробелы():
    assert normalize("  Ответственный\xa0 ") == "ответственный"
    assert normalize("ОТВЕТСТВЕННЫЙ") == normalize("ответственный")


# ── разбор таблиц ───────────────────────────────────────────────────────────
def test_обе_таблицы_страницы_разобраны():
    tables = parse_tables(PAGE)

    assert len(tables) == 2
    # colgroup и tbody на строки не влияют: строкой считается только tr.
    assert len(tables[0].rows) == 3
    assert len(tables[1].rows) == 2


def test_заголовки_читаются_и_из_th_и_из_td():
    tables = parse_tables(
        "<table><tbody>"
        "<tr><td>Задача</td><th>Ответственный</th></tr>"
        f"<tr><td>A</td><td>{_mention('aaa111')}</td></tr>"
        "</tbody></table>"
    )

    assert collect_mentions(tables, ["Ответственный"]) != []


# ── сбор упоминаний ─────────────────────────────────────────────────────────
def test_упоминания_собираются_по_имени_столбца_без_повторов():
    mentions = collect_mentions(parse_tables(PAGE), ["Ответственный"])

    # Порядок обхода документа, aaa111 встречается дважды и берётся один раз.
    assert [mention.key for mention in mentions] == ["aaa111", "bbb222"]


def test_имя_столбца_сверяется_без_учёта_регистра_и_пробелов():
    """В разметке заголовок — «Ответственный&nbsp;», в конфиге его напишут иначе."""
    mentions = collect_mentions(parse_tables(PAGE), ["  ответственный  "])

    assert [mention.key for mention in mentions] == ["aaa111", "bbb222"]


def test_разметка_упоминания_копируется_дословно():
    mentions = collect_mentions(parse_tables(PAGE), ["Ответственный"])

    assert mentions[0].markup == _mention("aaa111")


def test_несколько_столбцов_объединяются():
    storage = (
        "<table><tbody>"
        "<tr><th>Ответственный</th><th>Ревьюер</th></tr>"
        f"<tr><td>{_mention('aaa111')}</td><td>{_mention('bbb222')}</td></tr>"
        "</tbody></table>"
    )

    mentions = collect_mentions(parse_tables(storage), ["Ответственный", "Ревьюер"])

    assert [mention.key for mention in mentions] == ["aaa111", "bbb222"]


def test_неизвестный_столбец_даёт_пусто():
    assert collect_mentions(parse_tables(PAGE), ["Начальник"]) == []


@pytest.mark.parametrize("attr", ["ri:userkey", "ri:username", "ri:account-id"])
def test_опознаватель_берётся_из_любого_варианта_атрибута(attr):
    """Server/DC отдаёт userkey, старые версии username, Cloud account-id."""
    storage = (
        "<table><tbody>"
        "<tr><th>Ответственный</th></tr>"
        f'<tr><td><ac:link><ri:user {attr}="kkk555" /></ac:link></td></tr>'
        "</tbody></table>"
    )

    mentions = collect_mentions(parse_tables(storage), ["Ответственный"])

    assert [mention.key for mention in mentions] == ["kkk555"]


def test_пользователь_в_параметре_макроса_не_считается_упоминанием():
    """Виджет @человек — это всегда ac:link; ri:user встречается и в макросах."""
    storage = (
        "<table><tbody>"
        "<tr><th>Ответственный</th></tr>"
        '<tr><td><ac:structured-macro ac:name="profile">'
        '<ac:parameter ac:name="user"><ri:user ri:userkey="ddd444" /></ac:parameter>'
        "</ac:structured-macro></td></tr>"
        "</tbody></table>"
    )

    assert collect_mentions(parse_tables(storage), ["Ответственный"]) == []


# ── геометрия таблицы ───────────────────────────────────────────────────────
def test_colspan_в_заголовке_не_сбивает_столбец():
    storage = (
        "<table><tbody>"
        '<tr><th colspan="2">Задача</th><th>Ответственный</th></tr>'
        f"<tr><td>A</td><td>B</td><td>{_mention('aaa111')}</td></tr>"
        "</tbody></table>"
    )

    mentions = collect_mentions(parse_tables(storage), ["Ответственный"])

    assert [mention.key for mention in mentions] == ["aaa111"]


def test_rowspan_в_соседнем_столбце_не_сбивает_нумерацию():
    """Объединённая по вертикали ячейка оставляет в строке ниже меньше td."""
    storage = (
        "<table><tbody>"
        "<tr><th>Этап</th><th>Задача</th><th>Ответственный</th></tr>"
        f'<tr><td rowspan="2">Сборка</td><td>Собрать</td>'
        f"<td>{_mention('aaa111')}</td></tr>"
        f"<tr><td>Подписать</td><td>{_mention('bbb222')}</td></tr>"
        "</tbody></table>"
    )

    mentions = collect_mentions(parse_tables(storage), ["Ответственный"])

    assert [mention.key for mention in mentions] == ["aaa111", "bbb222"]


def test_вложенная_таблица_не_подмешивает_строки_во_внешнюю():
    inner = (
        "<table><tbody>"
        "<tr><th>Ответственный</th></tr>"
        f"<tr><td>{_mention('zzz999')}</td></tr>"
        "</tbody></table>"
    )
    storage = (
        "<table><tbody>"
        "<tr><th>Задача</th><th>Ответственный</th></tr>"
        f"<tr><td>{inner}</td><td>{_mention('aaa111')}</td></tr>"
        "</tbody></table>"
    )

    tables = parse_tables(storage)

    assert len(tables) == 2
    # Внешняя таблица — ровно две строки, вложенные tr в неё не попали.
    outer = max(tables, key=lambda table: len(table.rows[0]))
    assert len(outer.rows) == 2
    assert len(outer.rows[1]) == 2


# ── плейсхолдер ─────────────────────────────────────────────────────────────
def test_плейсхолдер_находится_по_первой_ячейке_строки():
    (cell,) = find_placeholders(parse_tables(PAGE), "Участники")

    assert cell.tag == "td"
    assert PAGE[cell.start : cell.end] == ""


def test_плейсхолдер_не_найден():
    assert find_placeholders(parse_tables(PAGE), "Ревьюеры") == []


def test_несколько_одноимённых_плейсхолдеров_возвращаются_все():
    """Чтобы вызывающий заметил неоднозначность, а не молча взял какой-то."""
    storage = (
        "<table><tbody>"
        "<tr><th>Участники</th><td>первый</td></tr>"
        "<tr><th>Участники</th><td>второй</td></tr>"
        "</tbody></table>"
    )

    found = find_placeholders(parse_tables(storage), "Участники")

    assert [storage[cell.start : cell.end] for cell in found] == ["первый", "второй"]


# ── запись ──────────────────────────────────────────────────────────────────
def test_запись_меняет_только_ячейку_плейсхолдера():
    tables = parse_tables(PAGE)
    mentions = collect_mentions(tables, ["Ответственный"])
    (cell,) = find_placeholders(tables, "Участники")

    result = splice(PAGE, cell, render_mentions(mentions))

    # Таблица-источник уцелела дословно.
    source_table = PAGE[PAGE.index("<table>") : PAGE.index("</table>") + len("</table>")]
    assert source_table in result
    # Выросла только подставленная часть.
    assert len(result) == len(PAGE) + len(render_mentions(mentions))


def test_записанное_читается_обратно_и_больше_не_требует_записи():
    """Это и есть защита от перезаписи на каждом проходе cron."""
    tables = parse_tables(PAGE)
    mentions = collect_mentions(tables, ["Ответственный"])
    (cell,) = find_placeholders(tables, "Участники")

    result = splice(PAGE, cell, render_mentions(mentions))

    tables_after = parse_tables(result)
    (cell_after,) = find_placeholders(tables_after, "Участники")
    assert [mention.key for mention in cell_after.mentions] == ["aaa111", "bbb222"]
    assert same_mentions(cell_after, mentions) is True


def test_повторная_запись_не_меняет_документ():
    tables = parse_tables(PAGE)
    mentions = collect_mentions(tables, ["Ответственный"])
    (cell,) = find_placeholders(tables, "Участники")
    once = splice(PAGE, cell, render_mentions(mentions))

    tables_after = parse_tables(once)
    (cell_after,) = find_placeholders(tables_after, "Участники")
    twice = splice(once, cell_after, render_mentions(mentions))

    assert twice == once


def test_пустой_сбор_очищает_ячейку():
    """Иначе выбывший из таблицы человек навсегда остался бы в плейсхолдере."""
    storage = "<table><tbody><tr><th>Участники</th><td>кто-то старый</td></tr></tbody></table>"
    (cell,) = find_placeholders(parse_tables(storage), "Участники")

    result = splice(storage, cell, render_mentions([]))

    assert "кто-то старый" not in result
    assert "<td></td>" in result


def test_сверка_замечает_изменение_состава():
    tables = parse_tables(PAGE)
    mentions = collect_mentions(tables, ["Ответственный"])
    (cell,) = find_placeholders(tables, "Участники")

    # Пустой плейсхолдер против непустого сбора — писать нужно.
    assert same_mentions(cell, mentions) is False


def test_плейсхолдер_в_собираемом_столбце_не_читается_как_данные():
    """Без exclude бот принял бы свой прошлый результат за данные таблицы."""
    storage = (
        "<table><tbody>"
        "<tr><th>Роль</th><th>Ответственный</th></tr>"
        f"<tr><td>Разработка</td><td>{_mention('aaa111')}</td></tr>"
        f"<tr><td>Участники</td><td>{_mention('ccc333')}</td></tr>"
        "</tbody></table>"
    )
    tables = parse_tables(storage)
    (placeholder,) = find_placeholders(tables, "Участники")

    without = collect_mentions(tables, ["Ответственный"])
    with_exclude = collect_mentions(tables, ["Ответственный"], exclude=placeholder)

    assert [mention.key for mention in without] == ["aaa111", "ccc333"]
    assert [mention.key for mention in with_exclude] == ["aaa111"]


def test_разделитель_настраивается():
    mentions = collect_mentions(parse_tables(PAGE), ["Ответственный"])

    rendered = render_mentions(mentions, separator=" ")

    assert rendered == "<p>" + _mention("aaa111") + " " + _mention("bbb222") + "</p>"


# ── наличие столбца ─────────────────────────────────────────────────────────
def test_наличие_столбца_отличается_от_его_пустоты():
    """По пустому результату collect_mentions «нет столбца» и «столбец пуст»
    неразличимы, а предупреждать нужно только в первом случае."""
    пустой = (
        "<table><tbody>"
        "<tr><th>Ответственный</th></tr>"
        "<tr><td>&nbsp;</td></tr>"
        "</tbody></table>"
    )

    assert has_columns(parse_tables(пустой), ["Ответственный"]) is True
    assert collect_mentions(parse_tables(пустой), ["Ответственный"]) == []


def test_столбца_нет_вовсе():
    assert has_columns(parse_tables(PAGE), ["Начальник"]) is False


def test_достаточно_одного_столбца_из_нескольких():
    assert has_columns(parse_tables(PAGE), ["Начальник", "Ответственный"]) is True


def test_наличие_столбца_сверяется_нестрого():
    assert has_columns(parse_tables(PAGE), ["  ОТВЕТСТВЕННЫЙ  "]) is True


def test_столбец_ищется_только_в_первой_строке():
    """Иначе совпадение в данных выглядело бы как заголовок."""
    storage = (
        "<table><tbody>"
        "<tr><th>Задача</th></tr>"
        "<tr><td>Ответственный</td></tr>"
        "</tbody></table>"
    )

    assert has_columns(parse_tables(storage), ["Ответственный"]) is False


def test_таблиц_нет_вовсе():
    assert has_columns(parse_tables("<p>просто текст</p>"), ["Ответственный"]) is False
