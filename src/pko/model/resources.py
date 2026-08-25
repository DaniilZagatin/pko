"""Контракты будущего runtime-контура. Ничего из этого сейчас не исполняется.

Стандарт требует от промышленной автономии того, чего статический анализ дать
не может: решения policy layer перед каждым внешним эффектом, подтверждения
человеком, передачи управления и неизменяемого журнала исполнения (§6.6–6.8).
PKO это не делает и делать вид не должен — `pko.standard.catalog` помечает
такие требования как `NEEDS_RUNTIME`.

Формы здесь описаны заранее по одной причине: когда runtime появится, у него
уже будет согласованный контракт, а до тех пор видно, чего именно не хватает.
Модуль намеренно состоит из одних определений — ни функции исполнения, ни
хранилища тут нет, и импорт его ничего не включает.

Выбранное хранилище следующей итерации — SQLite или JSONL внутри каталога
результата, подтверждение и handoff — локальный CLI. Это решение следующей
итерации, а не возможность текущей версии.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Решение policy layer перед действием (§6.6).
DECISION_ALLOW = "ALLOW"
DECISION_DENY = "DENY"
DECISION_CONFIRM = "CONFIRM"
DECISION_HANDOFF = "HANDOFF"
DECISION_SAFE_STOP = "SAFE_STOP"

POLICY_DECISIONS = (
    DECISION_ALLOW, DECISION_DENY, DECISION_CONFIRM, DECISION_HANDOFF, DECISION_SAFE_STOP,
)

# Состояние исполнения (§6.3, «условия завершения»).
RUN_SUCCEEDED = "SUCCEEDED"
RUN_PARTIAL = "PARTIAL"
RUN_STOPPED = "STOPPED"
RUN_HANDED_OFF = "HANDED_OFF"

RUN_STATES = (RUN_SUCCEEDED, RUN_PARTIAL, RUN_STOPPED, RUN_HANDED_OFF)


@dataclass(frozen=True)
class ActionProposal:
    """Что модель предлагает сделать. Не действие, а заявка на действие.

    Ключевое отличие от прямого вызова инструмента: предложение проходит через
    policy layer и только потом попадает исполнителю. Модель не вызывает SQL,
    HTTP или Python — она называет операцию из зарегистрированного каталога.
    """

    proposal_id: str
    operation_id: str          # идентификатор атомарной операции из модели PKO
    arguments: dict[str, Any] = field(default_factory=dict)
    requested_mode: str = "ASSIST"
    rationale: str = ""


@dataclass(frozen=True)
class PolicyDecision:
    """Решение по заявке: что разрешено и на каком основании (§6.6)."""

    policy_decision_id: str
    proposal_id: str
    decision: str              # одно из POLICY_DECISIONS
    matched_policies: tuple[str, ...] = ()
    reason: str = ""
    granted_mode: str = ""


@dataclass(frozen=True)
class Confirmation:
    """Подтверждение человеком, когда решение — CONFIRM (§6.4)."""

    confirmation_id: str
    proposal_id: str
    confirmed_by: str
    confirmed_at: str
    comment: str = ""


@dataclass(frozen=True)
class Handoff:
    """Передача управления человеку: почему и с каким контекстом (§6.7)."""

    handoff_id: str
    proposal_id: str
    to_role: str
    reason: str
    context_ref: str = ""


@dataclass(frozen=True)
class ExecutionEvent:
    """Запись append-only журнала (§6.8).

    Событие неизменяемо: журнал только дополняется. Отсутствие события не
    доказывает отсутствия действия — оно означает, что действие нельзя
    подтвердить и, значит, нельзя допустить автоматически.
    """

    event_id: str
    execution_id: str
    at: str
    kind: str                  # proposal | decision | confirmation | handoff | result
    payload: dict[str, Any] = field(default_factory=dict)
    previous_event_id: str = ""


@dataclass(frozen=True)
class ExecutionRecord:
    """Минимальная запись исполнения `BASIC` (§8.0.2)."""

    execution_id: str
    gate_card_ref: str
    started_at: str
    finished_at: str = ""
    state: str = RUN_SUCCEEDED
    events: tuple[str, ...] = ()
