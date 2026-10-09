"""
Parsing the Confluence storage format: find the rollout table, pull out the rows.

People word column headers differently, so some names are matched in full and
some by prefix. Both Russian and English wordings are recognised — the pages
this tool was written for are Russian, but the table may equally be in English.
The rollout table is the first one that has both required columns.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup, Tag

from .errors import ParseError
from .model import Row

JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]+-\d+\b")

# column header (lowercased) -> internal field name
COLUMNS = {
    "№": "num",
    "#": "num",
    "no": "num",
    "команда": "team",
    "team": "team",
    "кластер": "cluster",
    "cluster": "cluster",
    "статус": "status",
    "status": "status",
    "зависимость от релизов": "depends_on",
    "depends on": "depends_on",
    "depends on releases": "depends_on",
    "release dependencies": "depends_on",
    "релиз": "releases",
    "release": "releases",
    "releases": "releases",
    "задача на установку": "install_tasks",
    "install task": "install_tasks",
    "installation task": "install_tasks",
}
# Columns whose header is long and tends to drift — matched by prefix
COLUMN_PREFIXES = {
    "зависимости до внедрения": "prereqs",
    "dependencies before": "prereqs",
    "prerequisites": "prereqs",
    "before rollout": "prereqs",
    "зависимости после внедрения": "postreqs",
    "dependencies after": "postreqs",
    "after rollout": "postreqs",
}
REQUIRED = {"releases", "depends_on"}
KEY_COLUMNS = ("releases", "depends_on", "install_tasks", "prereqs", "postreqs")


def column_name(header: str) -> str | None:
    h = header.lower()
    if h in COLUMNS:
        return COLUMNS[h]
    for prefix, name in COLUMN_PREFIXES.items():
        if h.startswith(prefix):
            return name
    return None


def cell_text(cell: Tag) -> str:
    return " ".join(cell.get_text(" ", strip=True).split())


def cell_keys(cell: Tag) -> list[str]:
    """Jira keys from a cell: jira macros, links and plain text."""
    cell = BeautifulSoup(str(cell), "html.parser")  # a copy, so the original survives
    keys: list[str] = []
    for macro in cell.find_all("ac:structured-macro", attrs={"ac:name": "jira"}):
        param = macro.find("ac:parameter", attrs={"ac:name": "key"})
        if param:
            keys.append(param.get_text(strip=True))
        macro.decompose()  # so that serverId and friends are not caught by the regex
    for a in cell.find_all("a", href=True):
        keys += JIRA_KEY_RE.findall(a["href"])
    keys += JIRA_KEY_RE.findall(cell.get_text(" "))
    return list(dict.fromkeys(keys))  # dedupe, order preserved


def cell_status(cell: Tag) -> str:
    macro = cell.find("ac:structured-macro", attrs={"ac:name": "status"})
    if not macro:
        return cell_text(cell)
    title = macro.find("ac:parameter", attrs={"ac:name": "title"})
    return title.get_text(strip=True) if title else ""


def find_release_table(soup: BeautifulSoup) -> tuple[Tag, dict[int, str]]:
    """Look for a table whose header carries every required column."""
    for table in soup.find_all("table"):
        header = table.find("tr")
        if not header:
            continue
        mapping = {}
        for i, th in enumerate(header.find_all(["th", "td"], recursive=False)):
            name = column_name(cell_text(th))
            if name:
                mapping[i] = name
        if REQUIRED <= set(mapping.values()):
            return table, mapping
    raise ParseError("No rollout table found "
                     "(columns «Релиз»/«Release» and «Зависимость от релизов»/"
                     "«Depends on» are required)")


def parse_rows(storage: str) -> list[Row]:
    soup = BeautifulSoup(storage, "html.parser")
    table, mapping = find_release_table(soup)
    rows: list[Row] = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all(["td", "th"], recursive=False)
        if not cells:
            continue
        row = Row(num=str(len(rows) + 1))
        for i, cell in enumerate(cells):
            name = mapping.get(i)
            if not name:
                continue
            if cell.find("ac:placeholder"):
                row.unfilled.append(name)
            if name == "num":
                row.num = cell_text(cell) or row.num
            elif name == "status":
                row.status = cell_status(cell)
            elif name in KEY_COLUMNS:
                setattr(row, name, cell_keys(cell))
            else:
                setattr(row, name, cell_text(cell))
        rows.append(row)
    return rows
