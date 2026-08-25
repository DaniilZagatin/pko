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
from pko.model import taxonomy

# Кандидатом становится наблюдение, у которого есть собственная единица кода:
# точка входа, шаг траектории, компонент — либо ограничение исполнения.
# Раньше здесь стоял перечень видов, и находка на незнакомом стеке (обработчик
# UI-события, команда CLI) не попадала в модель вовсе.
CAPABILITY_PREDICATE = taxonomy.is_capability
CONSTRAINT_PREDICATE = taxonomy.is_constraint


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
        facets = fact.facets
        capability = CAPABILITY_PREDICATE(facets)
        if not capability and not CONSTRAINT_PREDICATE(facets):
            continue
        cid = _candidate_id(fact)
        if cid in by_key:
            by_key[cid].facts.append(fact)
            continue
        cand = Candidate(
            id=cid,
            type="CAPABILITY" if capability else "CONSTRAINT",
            subtype=fact.kind,
            name=_name(fact),
            group=_group(fact),
            facts=[fact],
        )
        by_key[cid] = cand
        out.append(cand)

    out.sort(key=lambda c: (c.type, c.subtype, c.id))
    return out


# Приставки прежних видов зафиксированы: идентификатор кандидата попадает в
# состав блока, и его смена показала бы все блоки изменившимися при сравнении
# версий. Для новых механизмов приставка выводится из самого механизма.
_LEGACY_PREFIX = {
    "ROUTE": "route",
    "GRAPH_NODE": "node",
    "TOOL": "tool",
    "MODULE": "module",
    "LIMIT": "limit",
    "ALLOWLIST": "allow",
}


def _candidate_id(fact: Fact) -> str:
    facets = fact.facets
    prefix = _LEGACY_PREFIX.get(fact.kind) or facets.mechanism or facets.category.lower()
    key = fact.key.strip().replace(" ", "_").replace("/", "-").lower()
    if CONSTRAINT_PREDICATE(facets):
        # Ограничение опознаётся именем параметра, а оно не уникально: `timeout`
        # HTTP-сервера и `timeout` вызова модели — разные ограничения разных
        # участков исполнения. Без места в идентификаторе они склеивались в
        # одного кандидата, и в отчёте оставалась одна политика с чужим
        # значением.
        return f"{prefix}:{_group(fact)}|{key}"
    return f"{prefix}:{key}"


def _name(fact: Fact) -> str:
    facets = fact.facets
    if facets.mechanism == "package":
        return fact.key.rsplit("/", 1)[-1]
    if facets.mechanism == "limit":
        return f"{fact.key} = {fact.value}"
    return fact.key


def _group(fact: Fact) -> str:
    """Пакет, к которому относится кандидат."""
    if fact.facets.mechanism == "package":
        return fact.key
    path = fact.path
    return path.rsplit("/", 1)[0] if "/" in path else path
