"""Кандидаты — единственное, что видит языковая модель.

Кандидат собирается только из фактов, у каждого есть устойчивый идентификатор.
Сборщик (GLM) обязан ссылаться на эти идентификаторы; объект, который ссылается
на несуществующего кандидата, отбрасывается. Так модель не может придумать блок,
которого нет в коде.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pko.extractors.base import Fact
from pko.extractors.runner import Extraction

CAPABILITY_KINDS = {"ROUTE", "GRAPH_NODE", "TOOL", "MODULE"}
CONSTRAINT_KINDS = {"LIMIT", "ALLOWLIST"}


@dataclass
class Candidate:
    """Техническая единица, найденная в коде."""

    id: str
    type: str          # CAPABILITY | CONSTRAINT
    subtype: str       # ROUTE | GRAPH_NODE | TOOL | MODULE | LIMIT | ALLOWLIST
    name: str
    group: str         # пакет или каталог — основа для группировки в BBB
    facts: list[Fact] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.facts[0].path if self.facts else ""

    @property
    def line(self) -> int | None:
        return self.facts[0].line if self.facts else None

    def to_prompt_dict(self) -> dict[str, Any]:
        """Вид кандидата для языковой модели: без текста кода."""
        return {
            "id": self.id,
            "subtype": self.subtype,
            "name": self.name,
            "group": self.group,
            "path": self.path,
        }


def build_candidates(extraction: Extraction) -> list[Candidate]:
    """Собрать кандидатов из фактов. Порядок детерминирован."""
    out: list[Candidate] = []
    by_key: dict[str, Candidate] = {}

    for fact in extraction.facts:
        if fact.kind not in CAPABILITY_KINDS and fact.kind not in CONSTRAINT_KINDS:
            continue
        cid = _candidate_id(fact)
        if cid in by_key:
            by_key[cid].facts.append(fact)
            continue
        cand = Candidate(
            id=cid,
            type="CAPABILITY" if fact.kind in CAPABILITY_KINDS else "CONSTRAINT",
            subtype=fact.kind,
            name=_name(fact),
            group=_group(fact),
            facts=[fact],
        )
        by_key[cid] = cand
        out.append(cand)

    out.sort(key=lambda c: (c.type, c.subtype, c.id))
    return out


def _candidate_id(fact: Fact) -> str:
    prefix = {
        "ROUTE": "route",
        "GRAPH_NODE": "node",
        "TOOL": "tool",
        "MODULE": "module",
        "LIMIT": "limit",
        "ALLOWLIST": "allow",
    }[fact.kind]
    key = fact.key.strip().replace(" ", "_").replace("/", "-").lower()
    return f"{prefix}:{key}"


def _name(fact: Fact) -> str:
    if fact.kind == "MODULE":
        return fact.key.rsplit("/", 1)[-1]
    if fact.kind == "LIMIT":
        return f"{fact.key} = {fact.value}"
    return fact.key


def _group(fact: Fact) -> str:
    """Пакет, к которому относится кандидат."""
    if fact.kind == "MODULE":
        return fact.key
    path = fact.path
    return path.rsplit("/", 1)[0] if "/" in path else path
