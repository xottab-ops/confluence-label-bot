"""Чтение и правка storage format Confluence: таблицы и @упоминания.

Тело страницы в Confluence Server/DC — XHTML с namespace `ac:`/`ri:`, которые
в самом фрагменте не объявлены, и с HTML-сущностями вроде `&nbsp;`. На таком
документе `xml.etree.ElementTree` падает, а тащить `lxml` ради одной задачи не
хочется.

Поэтому stdlib-овый `html.parser.HTMLParser` используется здесь не как
построитель дерева, а как токенайзер: он терпит и сущности, и незнакомые
namespace, а `getpos()` даёт позицию каждой конструкции. Из позиций собираются
**смещения** в исходной строке, и правка делается срезом — остальная страница
остаётся байт-в-байт. Полная пересборка документа парсером переформатировала бы
весь XHTML, и в истории версий страницы оказался бы нечитаемый diff.

Разметка упоминания тоже не пересобирается, а копируется подстрокой исходника:
тогда любой вариант, который отдал конкретный Confluence (`ri:userkey`,
`ri:username`, `ri:account-id`), переносится без разбора.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser

logger = logging.getLogger(__name__)

# Атрибуты <ri:user>, из которых берётся опознаватель человека, в порядке
# предпочтения: Server/DC отдаёт userkey, старые версии — username,
# Cloud — account-id.
_USER_KEY_ATTRS = ("ri:userkey", "ri:username", "ri:account-id")

_CELL_TAGS = frozenset({"td", "th"})


# ── публичные типы ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Mention:
    """Одно @упоминание: опознаватель для сверки и исходная разметка."""

    key: str
    markup: str


@dataclass(frozen=True)
class Cell:
    """Ячейка таблицы и границы её содержимого в исходной строке.

    start — сразу после открывающего тега, end — начало закрывающего: то есть
    ровно то, что лежит внутри ячейки, без самих тегов.
    """

    tag: str
    column: int
    start: int
    end: int
    text: str
    mentions: tuple[Mention, ...]

    @property
    def is_header(self) -> bool:
        return self.tag == "th"


@dataclass(frozen=True)
class Table:
    rows: tuple[tuple[Cell, ...], ...]


def normalize(text: str) -> str:
    """Привести текст к виду, в котором сверяются имена столбца и плейсхолдера.

    str.split() схлопывает любые пробелы, включая неразрывный из `&nbsp;`,
    которым Confluence щедро набивает ячейки.
    """
    return " ".join(text.split()).casefold()


# ── токенайзер ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Token:
    kind: str  # start | end | startend | text | other
    tag: str
    attrs: dict[str, str]
    start: int
    text: str


class _Tokenizer(HTMLParser):
    """Поток конструкций документа со смещениями в исходной строке.

    Дерево не строится: нужны только границы ячеек и упоминаний, а их безопаснее
    взять смещениями, чем перекладывать документ в узлы и обратно.
    """

    def __init__(self, storage: str) -> None:
        # convert_charrefs=False обязателен: иначе парсер склеивает текст с
        # сущностями и буферизует его, а смещения должны совпадать с исходником
        # символ в символ.
        super().__init__(convert_charrefs=False)
        # getpos() отдаёт номер строки и колонку — нужна абсолютная позиция.
        self._line_starts = [0]
        for index, char in enumerate(storage):
            if char == "\n":
                self._line_starts.append(index + 1)
        self.tokens: list[_Token] = []

    def _add(
        self,
        kind: str,
        tag: str = "",
        attrs: Sequence[tuple[str, str | None]] | None = None,
        text: str = "",
    ) -> None:
        line, column = self.getpos()
        self.tokens.append(
            _Token(
                kind=kind,
                tag=tag,
                attrs={name: value or "" for name, value in (attrs or ())},
                start=self._line_starts[line - 1] + column,
                text=text,
            )
        )

    def handle_starttag(self, tag, attrs):  # noqa: D102 — контракт HTMLParser
        self._add("start", tag, attrs)

    def handle_endtag(self, tag):  # noqa: D102
        self._add("end", tag)

    def handle_startendtag(self, tag, attrs):  # noqa: D102
        # Переопределено намеренно: базовый класс разложил бы <ri:user/> на
        # start+end с одним и тем же смещением, и границы поехали бы.
        self._add("startend", tag, attrs)

    def handle_data(self, data):  # noqa: D102
        self._add("text", text=data)

    def handle_entityref(self, name):  # noqa: D102
        self._add("text", text=html.unescape(f"&{name};"))

    def handle_charref(self, name):  # noqa: D102
        self._add("text", text=html.unescape(f"&#{name};"))

    def unknown_decl(self, data):  # noqa: D102
        # <![CDATA[…]]> — в него storage format заворачивает тела макросов.
        prefix = "CDATA["
        self._add("text", text=data[len(prefix) :] if data.startswith(prefix) else "")

    def handle_comment(self, data):  # noqa: D102
        self._add("other")

    def handle_decl(self, decl):  # noqa: D102
        self._add("other")

    def handle_pi(self, data):  # noqa: D102
        self._add("other")


def _tokenize(storage: str) -> list[_Token]:
    parser = _Tokenizer(storage)
    parser.feed(storage)
    parser.close()
    return parser.tokens


def _token_end(tokens: Sequence[_Token], index: int, total: int) -> int:
    """Где кончается конструкция — там, где начинается следующая."""
    return tokens[index + 1].start if index + 1 < len(tokens) else total


def _user_key(attrs: dict[str, str]) -> str | None:
    for name in _USER_KEY_ATTRS:
        value = attrs.get(name, "").strip()
        if value:
            return value
    return None


def _find_close(tokens: Sequence[_Token], start: int, limit: int, tag: str) -> int | None:
    """Индекс закрывающего тега для tokens[start], с учётом вложенности."""
    depth = 0
    for index in range(start + 1, limit):
        token = tokens[index]
        if token.tag != tag:
            continue
        if token.kind == "start":
            depth += 1
        elif token.kind == "end":
            if depth == 0:
                return index
            depth -= 1
    return None


def _text_of(tokens: Sequence[_Token], first: int, last: int) -> str:
    return "".join(token.text for token in tokens[first:last] if token.kind == "text")


def _mentions_of(
    storage: str, tokens: Sequence[_Token], first: int, last: int
) -> tuple[Mention, ...]:
    """Упоминания внутри диапазона конструкций.

    Берутся только <ri:user> внутри <ac:link> — именно так выглядит стандартный
    виджет @человек. Тот же <ri:user> встречается и в параметрах макросов, и
    тянуть людей оттуда не нужно.
    """
    total = len(storage)
    mentions: list[Mention] = []
    index = first

    while index < last:
        token = tokens[index]
        if not (token.kind == "start" and token.tag == "ac:link"):
            index += 1
            continue

        close = _find_close(tokens, index, last, "ac:link")
        if close is None:
            index += 1
            continue

        key = None
        for inner in tokens[index + 1 : close]:
            if inner.tag == "ri:user" and inner.kind in {"start", "startend"}:
                key = _user_key(inner.attrs)
                break
        if key:
            mentions.append(
                Mention(key=key, markup=storage[token.start : _token_end(tokens, close, total)])
            )
        index = close + 1

    return tuple(mentions)


# ── разбор таблиц ───────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _RawCell:
    tag: str
    start: int
    end: int
    text: str
    mentions: tuple[Mention, ...]
    colspan: int
    rowspan: int


@dataclass
class _Frame:
    """Одна таблица в разборе. Вложенные таблицы держатся стеком таких рамок."""

    rows: list[list[_RawCell]] = field(default_factory=list)
    row: list[_RawCell] | None = None
    open_tag: str | None = None
    open_start: int = 0
    open_index: int = 0
    open_attrs: dict[str, str] = field(default_factory=dict)


def _span(attrs: dict[str, str], name: str) -> int:
    raw = attrs.get(name, "").strip()
    return max(int(raw), 1) if raw.isdigit() else 1


def _build_table(raw_rows: Sequence[Sequence[_RawCell]]) -> Table:
    """Разложить ячейки по столбцам с учётом colspan и rowspan.

    Без учёта rowspan объединённая по вертикали ячейка в соседнем столбце
    сдвинула бы нумерацию во всех строках ниже, и бот читал бы не тот столбец.
    """
    rows: list[tuple[Cell, ...]] = []
    # Столбец → сколько строк он ещё занят ячейкой с rowspan.
    occupied: dict[int, int] = {}

    for raw_row in raw_rows:
        cells: list[Cell] = []
        column = 0
        for raw in raw_row:
            while occupied.get(column, 0) > 0:
                column += 1
            cells.append(
                Cell(
                    tag=raw.tag,
                    column=column,
                    start=raw.start,
                    end=raw.end,
                    text=raw.text,
                    mentions=raw.mentions,
                )
            )
            if raw.rowspan > 1:
                for occupied_column in range(column, column + raw.colspan):
                    occupied[occupied_column] = raw.rowspan
            column += raw.colspan
        rows.append(tuple(cells))

        # Конец строки: каждый счётчик на строку ближе к истечению.
        for occupied_column in list(occupied):
            occupied[occupied_column] -= 1
            if occupied[occupied_column] <= 0:
                del occupied[occupied_column]

    return Table(rows=tuple(rows))


def parse_tables(storage: str) -> list[Table]:
    """Разобрать все таблицы тела страницы, сохранив смещения ячеек.

    Вложенные таблицы разбираются как отдельные: таблица внутри ячейки не должна
    подмешивать свои строки во внешнюю.
    """
    tokens = _tokenize(storage)
    total = len(storage)
    tables: list[Table] = []
    stack: list[_Frame] = []

    for index, token in enumerate(tokens):
        tag = token.tag

        if token.kind == "start":
            if tag == "table":
                stack.append(_Frame())
            elif stack:
                frame = stack[-1]
                if tag == "tr":
                    frame.row = []
                elif tag in _CELL_TAGS and frame.row is not None:
                    frame.open_tag = tag
                    frame.open_attrs = token.attrs
                    frame.open_index = index
                    # Содержимое начинается там, где кончается открывающий тег.
                    frame.open_start = _token_end(tokens, index, total)

        elif token.kind == "end":
            if tag == "table":
                if stack:
                    tables.append(_build_table(stack.pop().rows))
            elif stack:
                frame = stack[-1]
                if tag == "tr":
                    if frame.row is not None:
                        frame.rows.append(frame.row)
                    frame.row = None
                elif tag in _CELL_TAGS and frame.open_tag == tag and frame.row is not None:
                    frame.row.append(
                        _RawCell(
                            tag=tag,
                            start=frame.open_start,
                            end=token.start,
                            text=_text_of(tokens, frame.open_index + 1, index),
                            mentions=_mentions_of(storage, tokens, frame.open_index + 1, index),
                            colspan=_span(frame.open_attrs, "colspan"),
                            rowspan=_span(frame.open_attrs, "rowspan"),
                        )
                    )
                    frame.open_tag = None

    # Незакрытая таблица — битая разметка, но терять уже собранные строки незачем.
    while stack:
        tables.append(_build_table(stack.pop().rows))

    return tables


# ── сбор и запись ───────────────────────────────────────────────────────────
def collect_mentions(
    tables: Sequence[Table],
    columns: Sequence[str],
    *,
    exclude: Cell | None = None,
) -> list[Mention]:
    """Упоминания из столбцов с заданными заголовками, по всем таблицам.

    Заголовком считается первая строка таблицы, а заголовочной ячейкой — и `th`,
    и `td`: таблицы в Confluence бывают и без `th`. Результат — объединение по
    всем таблицам и столбцам, без повторов, в порядке обхода документа.

    exclude — ячейка-плейсхолдер. Если она попала в тот же столбец (например,
    таблица и приёмник на странице одни), без неё бот вычитал бы собственный
    прошлый результат и считал его данными.
    """
    wanted = {normalize(name) for name in columns}
    mentions: list[Mention] = []
    seen: set[str] = set()

    for table in tables:
        if not table.rows:
            continue
        header, *body = table.rows
        indices = {cell.column for cell in header if normalize(cell.text) in wanted}
        if not indices:
            continue
        for row in body:
            for cell in row:
                if cell.column not in indices:
                    continue
                if exclude is not None and cell.start == exclude.start:
                    continue
                for mention in cell.mentions:
                    if mention.key in seen:
                        continue
                    seen.add(mention.key)
                    mentions.append(mention)

    return mentions


def has_columns(tables: Sequence[Table], columns: Sequence[str]) -> bool:
    """Нашёлся ли хоть один из столбцов в заголовке хоть одной таблицы.

    Нужно, чтобы отличить «столбца на странице нет» (разметка не та, о чём
    стоит предупредить) от «столбец есть, но пустой» (нормальное состояние).
    По одному лишь пустому результату collect_mentions это неразличимо.
    """
    wanted = {normalize(name) for name in columns}
    return any(
        normalize(cell.text) in wanted
        for table in tables
        if table.rows
        for cell in table.rows[0]
    )


def find_placeholders(tables: Sequence[Table], name: str) -> list[Cell]:
    """Все ячейки-приёмники: вторые в строках, чья первая ячейка равна name.

    Первая ячейка бывает и `th` (так устроена левая колонка Page Properties), и
    `td` — принимаются обе. Возвращается список, а не одна ячейка, чтобы
    вызывающий заметил неоднозначность и предупредил о ней.
    """
    wanted = normalize(name)
    found: list[Cell] = []
    for table in tables:
        for row in table.rows:
            if len(row) >= 2 and normalize(row[0].text) == wanted:
                found.append(row[1])
    return found


def render_mentions(mentions: Sequence[Mention], *, separator: str = ", ") -> str:
    """XHTML для ячейки-приёмника.

    Разметка упоминаний копируется из исходника как есть, поэтому здесь только
    разделитель и абзац вокруг — ровно то, что получилось бы, набери человек
    упоминания руками. Пустой список даёт пустую ячейку: иначе удалённый из
    таблицы человек навсегда остался бы в плейсхолдере.
    """
    if not mentions:
        return ""
    return "<p>" + separator.join(mention.markup for mention in mentions) + "</p>"


def splice(storage: str, cell: Cell, content: str) -> str:
    """Подменить содержимое ячейки, не тронув остальную страницу."""
    return storage[: cell.start] + content + storage[cell.end :]


def same_mentions(cell: Cell, mentions: Sequence[Mention]) -> bool:
    """Совпадает ли уже лежащее в ячейке с тем, что собрано.

    Сверка по опознавателям, а не по разметке: иначе лишний пробел или другой
    порядок атрибутов заставляли бы бота перезаписывать страницу на каждом
    проходе cron — а это новая версия и письмо «вас упомянули» всем из таблицы.
    """
    return [mention.key for mention in cell.mentions] == [mention.key for mention in mentions]
