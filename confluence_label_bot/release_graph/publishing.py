"""
The picture onto the page: upload it as an attachment and put it where
<graph_placeholder> sits.

Running it twice is safe: the placeholder is gone by then, but our own picture
is found by attachment name, so the page body is left alone — a new version of
the file is enough.

Поверх relgraph здесь добавлен JSON плана: свёрнутый макрос `code` сразу под
картинкой. Он опознаётся по заголовку `<имя вложения>.json`, поэтому повторный
проход обновляет его на месте, а не плодит копии. Сравнение — по разобранным
данным, а не по тексту: Confluence при сохранении переписывает атрибуты
макроса, и текстовое сравнение давало бы правку на каждом проходе.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePath
from typing import Any

from .model import Plan, Row

COMMENT = "Release dependency graph"

PLACEHOLDER = "placeholder"   # marker found and replaced
EXISTING = "existing"         # no marker, but our picture is already there
MISSING = "missing"           # neither of the two


@dataclass
class Insertion:
    storage: str
    kind: str
    count: int = 0


def placeholder_re(name: str) -> re.Pattern:
    """<name>, &lt;name&gt; and <ac:placeholder>name</ac:placeholder> — every way Confluence stores it."""
    n = re.escape(name)
    return re.compile(
        rf"<ac:placeholder[^>]*>\s*(?:&lt;|<)?\s*{n}\s*(?:&gt;|>)?\s*</ac:placeholder>"
        rf"|(?:&lt;|<)\s*{n}\s*/?\s*(?:&gt;|>)",
        re.IGNORECASE)


def image_re(filename: str) -> re.Pattern:
    return re.compile(
        rf"<ac:image[^>]*>\s*<ri:attachment[^>]*ri:filename=\"{re.escape(filename)}\"[^>]*/?>"
        rf"\s*(?:</ri:attachment>\s*)?</ac:image>",
        re.IGNORECASE)


def image_storage(filename: str, width: int | None = None) -> str:
    attrs = ' ac:align="center" ac:layout="center"'
    if width:
        attrs += f' ac:width="{width}"'
    return f'<ac:image{attrs}><ri:attachment ri:filename="{filename}" /></ac:image>'


def insert_image(storage: str, filename: str, placeholder: str,
                 width: int | None = None) -> Insertion:
    img = image_storage(filename, width)
    new, count = placeholder_re(placeholder).subn(img, storage)
    if count:
        return Insertion(new, PLACEHOLDER, count)
    new, count = image_re(filename).subn(img, storage)
    if count:
        return Insertion(new, EXISTING, count)
    return Insertion(storage, MISSING)


# ── JSON плана под картинкой ────────────────────────────────────────────────
# Макрос code вложенных макросов не содержит, поэтому его конец — первый же
# закрывающий тег. Наш JSON сам такого тега содержать не может, см. dump_json.
_CODE_MACRO_RE = re.compile(
    r"<ac:structured-macro\b[^>]*\bac:name=\"code\"[^>]*>"
    r"(?:(?!</ac:structured-macro>).)*</ac:structured-macro>",
    re.DOTALL | re.IGNORECASE)
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_BODY_RE = re.compile(r"<ac:plain-text-body>(.*?)</ac:plain-text-body>", re.DOTALL)


def json_title(filename: str) -> str:
    """Заголовок макроса — от имени вложения: release-graph.png → release-graph.json."""
    return f"{PurePath(filename).stem}.json"


def plan_data(rows: list[Row], plan: Plan) -> dict[str, Any]:
    """То же, что relgraph кладёт в plan.json: строки и план вместе.

    Круг через json нужен для сравнения: кортежи рёбер плана после разбора
    со страницы возвращаются списками, и без нормализации данные никогда не
    совпали бы с прочитанными.
    """
    data = {"rows": [asdict(r) for r in rows], "plan": asdict(plan)}
    return json.loads(json.dumps(data, ensure_ascii=False))


def dump_json(data: dict[str, Any]) -> str:
    """JSON для CDATA.

    `>` в JSON встречается только внутри строк, поэтому замена на `\\u003e`
    данных не меняет, зато исключает и `]]>`, обрывающий CDATA, и
    `</ac:structured-macro>`, по которому ищется конец макроса.
    """
    return json.dumps(data, ensure_ascii=False, indent=2).replace(">", "\\u003e")


def json_storage(title: str, data: dict[str, Any]) -> str:
    # collapse — чтобы сотни строк JSON не раздвигали страницу под картинкой.
    return (
        '<ac:structured-macro ac:name="code">'
        f'<ac:parameter ac:name="title">{title}</ac:parameter>'
        '<ac:parameter ac:name="language">js</ac:parameter>'
        '<ac:parameter ac:name="collapse">true</ac:parameter>'
        f'<ac:plain-text-body><![CDATA[{dump_json(data)}]]></ac:plain-text-body>'
        '</ac:structured-macro>'
    )


def find_json(storage: str, title: str) -> tuple[re.Match | None, Any]:
    """Наш макрос по заголовку и разобранный JSON из него.

    Битый JSON (поправили руками) возвращается как None — это просто «данные
    отличаются», и макрос будет перезаписан.
    """
    wanted = re.compile(
        rf"<ac:parameter\s+ac:name=\"title\"\s*>\s*{re.escape(title)}\s*</ac:parameter>")
    for match in _CODE_MACRO_RE.finditer(storage):
        if not wanted.search(match.group(0)):
            continue
        body = _BODY_RE.search(match.group(0))
        text = "".join(_CDATA_RE.findall(body.group(1))) if body else ""
        try:
            return match, json.loads(text)
        except ValueError:
            return match, None
    return None, None


def insert_json(storage: str, filename: str, data: dict[str, Any]) -> tuple[str, bool]:
    """Положить JSON под картинку. Возвращает (тело, изменились ли данные).

    Вызывается после insert_image, когда картинка в теле уже стоит. Есть наш
    макрос — обновляется на месте, где бы его ни держали; нет — ставится сразу
    за первой картинкой.
    """
    title = json_title(filename)
    macro, old = find_json(storage, title)
    if macro is not None:
        if old == data:
            return storage, False
        return storage[:macro.start()] + json_storage(title, data) + storage[macro.end():], True

    image = image_re(filename).search(storage)
    if image is None:
        return storage, False
    # Маркер обычно стоит в своём <p>, а блочный макрос внутри абзаца — это
    # невалидная разметка, которую редактор потом перекладывает по-своему.
    at = image.end()
    tail = re.match(r"\s*</p>", storage[at:])
    if tail:
        at += tail.end()
    return storage[:at] + json_storage(title, data) + storage[at:], True
