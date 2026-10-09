"""
What we treat as data: a row of the rollout table and the plan built from it.

Row is what the page says, Plan is what planning.build_plan derived from the
rows. Both dataclasses serialise into plan.json via dataclasses.asdict.

A status cell is kept verbatim (the report shows it as written), while
comparisons go through status_code, which maps both Russian and English
wordings onto DONE / CANCELLED.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DONE = "done"
CANCELLED = "cancelled"

# how a status cell may be worded on the page -> canonical code
STATUS_ALIASES = {
    "ЗАВЕРШЕНО": DONE,
    "ВЫПОЛНЕНО": DONE,
    "DONE": DONE,
    "COMPLETED": DONE,
    "ОТМЕНЕНО": CANCELLED,
    "ОТМЕНЁН": CANCELLED,
    "CANCELLED": CANCELLED,
    "CANCELED": CANCELLED,
}
FINISHED = {DONE, CANCELLED}   # nothing left to install in such a row


def status_code(status: str) -> str:
    """Canonical status code, or an empty string for anything else."""
    return STATUS_ALIASES.get(status.strip().upper(), "")


@dataclass
class Row:
    num: str
    team: str = ""
    cluster: str = ""
    status: str = ""
    releases: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    install_tasks: list[str] = field(default_factory=list)
    prereqs: list[str] = field(default_factory=list)   # before rollout: settings, roles
    postreqs: list[str] = field(default_factory=list)  # after rollout: CCT, BFF
    unfilled: list[str] = field(default_factory=list)  # columns left as placeholders

    @property
    def node_id(self) -> str:
        return f"n{self.num}"

    @property
    def label(self) -> str:
        return f"{self.num}. {self.team} ({self.cluster})"

    @property
    def code(self) -> str:
        """Status as a canonical code — the form every comparison uses."""
        return status_code(self.status)


@dataclass
class Plan:
    waves: list[list[str]]                   # row numbers, wave by wave
    edges: list[tuple[str, str, list[str]]]  # (from, to, keys behind the link)
    external: dict[str, list[str]]           # row -> dependencies outside this rollout
    errors: list[str]
    warnings: list[str]
    entry_points: list[str] = field(default_factory=list)  # graph roots
    ready: list[str] = field(default_factory=list)         # can be started right now
    blocked: list[str] = field(default_factory=list)       # depend on a cancelled row
    prereq_edges: list[tuple[str, str]] = field(default_factory=list)  # (key, row)


def row_state(row: Row, plan: Plan) -> str:
    """A row's state in one word — it colours the report, the graph and Mermaid."""
    if row.code == DONE:
        return "done"
    if row.code == CANCELLED:
        return "cancelled"
    if row.num in plan.blocked:
        return "blocked"
    if row.num in plan.ready:
        return "ready"
    return "plain"
