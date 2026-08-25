"""Готовность к промышленной автономии: по областям, а не одним процентом.

BASIC Gate отвечает на вопрос «можно ли запускать пилот». Вопрос «готова ли
система к промышленному запуску» другой, и смешивать их опасно: профиль FULL
означает, что промышленные требования §6 *применяются*, а не что они
*выполнены*. Раньше отчёт печатал `машинный уровень FULL_RESOURCE_MODEL` и тем
самым подтверждал соответствие контуру, которого нет.

Оценка собирается по областям стандарта, и у каждой области есть основание,
доказательства и следующее действие. Процент готовности здесь намеренно не
считается: он создаёт ложную точность там, где половина областей вообще не
проверяется статически. Вместо процента — перечень того, что мешает.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pko.standard import catalog

# Состояние области.
READY = "READY"                # проверено, требование выполняется
PARTIAL = "PARTIAL"            # проверено частично, границы названы
MISSING = "MISSING"            # можно проверить статически, но не выполняется
NEEDS_RUNTIME = "NEEDS_RUNTIME"  # доказать по коду нельзя

# Порядок важности: область в худшем состоянии определяет состояние целого.
_SEVERITY = {READY: 0, PARTIAL: 1, MISSING: 2, NEEDS_RUNTIME: 3}

# Что делать дальше по каждому состоянию каталога.
_NEXT_ACTION = {
    catalog.CHECKED: "",
    catalog.PARTIAL: "дополнить источник, чтобы требование проверялось целиком",
    catalog.NOT_CHECKED: "реализовать статическую проверку требования",
    catalog.NEEDS_RUNTIME: "требуется исполняющий контур: по коду это не доказывается",
}

_STATE_FROM_CATALOG = {
    catalog.CHECKED: READY,
    catalog.PARTIAL: PARTIAL,
    catalog.NOT_CHECKED: MISSING,
    catalog.NEEDS_RUNTIME: NEEDS_RUNTIME,
}


@dataclass
class Area:
    """Одна область готовности со своим основанием и следующим шагом."""

    name: str
    state: str
    basis: str
    requirements: list[str] = field(default_factory=list)
    next_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "area": self.name,
            "state": self.state,
            "basis": self.basis,
            "requirements": self.requirements,
            "next_action": self.next_action,
        }


@dataclass
class Readiness:
    """Готовность к FULL: состояние, области и то, что мешает."""

    required: bool
    status: str
    summary: str
    areas: list[Area] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "pko-full-readiness/0.1",
            "full_required": self.required,
            "status": self.status,
            "summary": self.summary,
            "areas": [a.to_dict() for a in self.areas],
            "blocking_requirements": self.blocking,
        }


def assess(profile_value: str, model_counts: dict[str, int] | None = None) -> Readiness:
    """Собрать оценку готовности к промышленному контуру.

    `profile_value` — результат профилирования (`BASIC`/`FULL`). Оценка
    считается всегда: даже пилоту полезно видеть, чего не хватит для
    промышленного запуска, — но её статус зависит от того, требуется ли FULL
    уже сейчас.
    """
    counts = model_counts or {}
    areas = _areas()
    required = profile_value == catalog.FULL
    blocking = [r.id for r in catalog.blocking_for_full()]

    worst = max((_SEVERITY[a.state] for a in areas), default=0)
    if worst == 0:
        status = READY
    elif required:
        # Профиль требует промышленный контур, а он не подтверждён. Это не
        # отказ в допуске — BASIC Gate решает отдельно, — но и не соответствие.
        status = "NOT_READY"
    else:
        status = "NOT_REQUIRED"

    summary = _summary(status, required, blocking, counts)
    return Readiness(required=required, status=status, summary=summary,
                     areas=areas, blocking=blocking)


def _areas() -> list[Area]:
    """Область — худшее состояние среди её требований, с названным основанием."""
    grouped: dict[str, list[catalog.Requirement]] = {}
    for requirement in catalog.REQUIREMENTS:
        grouped.setdefault(requirement.area, []).append(requirement)

    areas: list[Area] = []
    for name, requirements in grouped.items():
        states = [_STATE_FROM_CATALOG[r.state] for r in requirements]
        state = max(states, key=lambda s: _SEVERITY[s])
        worst = next(r for r in requirements if _STATE_FROM_CATALOG[r.state] == state)
        basis = _basis(worst)
        areas.append(Area(
            name=name,
            state=state,
            basis=basis,
            requirements=[r.id for r in requirements],
            next_action=_NEXT_ACTION.get(worst.state, ""),
        ))
    areas.sort(key=lambda a: (-_SEVERITY[a.state], a.name))
    return areas


def _basis(requirement: catalog.Requirement) -> str:
    """Почему область в таком состоянии — словами, а не прочерком."""
    if requirement.limitation:
        return requirement.limitation
    if requirement.state == catalog.NEEDS_RUNTIME:
        return "подтверждается только исполнением: у PKO нет исполняющего контура"
    if requirement.state == catalog.NOT_CHECKED:
        return "статическая проверка этого требования пока не реализована"
    return requirement.source or "проверяется детерминированно"


def _summary(status: str, required: bool, blocking: list[str],
             counts: dict[str, int]) -> str:
    objects = sum(counts.get(kind, 0) for kind in ("BBB", "AO", "GUARDRAIL"))
    tail = f" Восстановлено объектов управления: {objects}." if objects else ""
    if status == READY:
        return "Все области промышленного контура подтверждены." + tail
    if required:
        return (
            f"Профиль требует промышленных требований §6, но подтвердить их по коду "
            f"нельзя: не выполнено требований — {len(blocking)}. Это оценка готовности, "
            f"а не отказ в допуске: решение BASIC Gate выносится отдельно." + tail
        )
    return (
        f"Промышленные требования §6 сейчас не применяются. Если профиль сменится на "
        f"FULL, потребуется закрыть требований: {len(blocking)}." + tail
    )
