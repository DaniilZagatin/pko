"""Запись допуска `BASIC_RECORD` — контракт §8.0.1 целиком, в одном месте.

Раньше поля контракта существовали только в Markdown-карточке, и часть из них
не существовала вовсе: разрешённый scope печатался как «весь проанализированный
код», запрещённые эффекты не печатались, а срок действия решения и условия его
инвалидирования не назывались нигде. Читатель не мог ответить на вопрос «на что
именно выдан допуск и когда он перестаёт действовать».

Здесь запись собирается один раз и одинаково питает `basic_gate.json` и
Markdown-карточку: два вида одной записи не должны расходиться.

Чего здесь нет и не будет: подтверждения, что ограничения соблюдались при
исполнении. Запись фиксирует объявленную границу, а не её соблюдение —
§8.0.2 требует записи о каждом запуске, и её PKO не производит
(`MACH-BASIC-EXEC` в `pko.standard.catalog`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pko.gate.decide import NO_DECISION, GateDecision
from pko.gate.evaluate import CheckResult
from pko.model.schema import PkoModel

# Что делает решение недействительным. Список закрытый и одинаковый для всех
# запусков: срок действия записи задаётся не датой, а версией реализации.
INVALIDATION = (
    "изменился коммит реализации — проверки относятся к другому коду",
    "изменилась существенная конфигурация из снимка реализации",
    "изменился business_intent.yaml: границы, режим или запрещённые эффекты",
    "изменился набор проверок Gate или их вычислители",
)

UNSET = "не задано владельцем"


@dataclass
class Scope:
    """Разрешённый scope §8.0.1: что входит, что исключено, что запрещено."""

    in_scope: list[str] = field(default_factory=list)
    out_of_scope: list[str] = field(default_factory=list)
    environment: str = ""
    cohort: str = ""
    forbidden_effects: list[str] = field(default_factory=list)
    analysed_perimeter: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "in_scope": self.in_scope,
            "out_of_scope": self.out_of_scope,
            "environment": self.environment,
            "cohort": self.cohort,
            "forbidden_effects": self.forbidden_effects,
            "analysed_perimeter": self.analysed_perimeter,
        }


@dataclass
class ImplementationSnapshot:
    """Точная версия кода и существенной конфигурации (§8.0.1, `implementation_ref`)."""

    ref: str = ""
    commit: str = ""
    commit_date: str = ""
    branch: str = ""
    repo: str = ""
    config_files: list[str] = field(default_factory=list)
    config_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "commit": self.commit,
            "commit_date": self.commit_date,
            "branch": self.branch,
            "repo": self.repo,
            "config_files": self.config_files,
            "config_note": self.config_note,
        }


@dataclass
class Validity:
    """Срок действия решения и условия инвалидирования (§8.0.1, `decision`)."""

    decided_by: str
    decided_at: str
    bound_to: str
    invalidated_by: tuple[str, ...] = INVALIDATION

    def to_dict(self) -> dict[str, Any]:
        return {
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
            "bound_to": self.bound_to,
            "invalidated_by": list(self.invalidated_by),
        }


@dataclass
class BasicRecord:
    """Все поля §8.0.1. Порядок полей — порядок таблицы стандарта."""

    record_version: str
    profile: dict[str, Any]
    decision_boundary: str
    purpose_and_result: dict[str, str]
    business_owner: str
    scope: Scope
    requested_mode: str
    implementation: ImplementationSnapshot
    checks: list[dict[str, Any]]
    restrictions: list[str]
    decision: dict[str, Any]
    validity: Validity
    gaps: list[str] = field(default_factory=list)

    def to_dict(self, generated_at: str = "") -> dict[str, Any]:
        return {
            "schema": "pko-basic-gate/0.2",
            "standard": "Автономный процесс v1.1 §8.0.1",
            "generated_at": generated_at,
            "record_version": self.record_version,
            "profile": self.profile,
            "decision_boundary": self.decision_boundary,
            "purpose_and_result": self.purpose_and_result,
            "business_owner": self.business_owner,
            "scope": self.scope.to_dict(),
            "requested_mode": self.requested_mode,
            "implementation_ref": self.implementation.ref,
            "implementation": self.implementation.to_dict(),
            "checks": self.checks,
            "restrictions": self.restrictions,
            "decision": self.decision,
            "validity": self.validity.to_dict(),
            "record_gaps": self.gaps,
        }


def build_record(
    model: PkoModel,
    results: list[CheckResult],
    decision: GateDecision,
    intent: dict[str, Any] | None,
    generated_at: str,
    config_files: list[str] | None = None,
    record_gaps: list[str] | None = None,
) -> BasicRecord:
    """Собрать запись допуска по §8.0.1 из результатов анализа."""
    intent = intent or {}
    meta = model.meta
    commit = str(meta.get("commit", ""))

    return BasicRecord(
        record_version=f"{meta.get('version_label', 'current')}-{commit[:8]}",
        profile=decision.profile,
        # Граница решения не подставляется по умолчанию. Раньше в незаполненное
        # поле печаталось `END_TO_END_PROCESS`, и запись утверждала, что допуск
        # выдан на весь процесс, — при том, что владелец границу не называл.
        decision_boundary=str(intent.get("decision_boundary") or UNSET),
        purpose_and_result=_purpose(intent),
        business_owner=str(intent.get("business_owner") or UNSET),
        scope=_scope(intent, model),
        requested_mode=decision.requested_mode,
        implementation=_snapshot(meta, decision, config_files or []),
        checks=[r.to_dict() for r in results],
        restrictions=_restrictions(decision),
        decision=decision.to_dict(),
        validity=_validity(decision, meta, generated_at),
        gaps=list(record_gaps or []),
    )


def _purpose(intent: dict[str, Any]) -> dict[str, str]:
    """Назначение и оба исхода. Успешный исход без корректной остановки — половина ответа."""
    return {
        "client": str(intent.get("client") or UNSET),
        "purpose": str(intent.get("business_meaning") or UNSET),
        "success_outcome": str(intent.get("target_state") or UNSET),
        "stopped_outcome": str(intent.get("stopped_state") or UNSET),
    }


def _scope(intent: dict[str, Any], model: PkoModel) -> Scope:
    """Разрешённый scope. Периметр анализа — не то же самое, что разрешённый scope.

    Печатать «весь проанализированный код» в поле `in_scope` неверно: это
    граница разбора, а не граница допуска. Владелец задаёт вторую, PKO знает
    только первую, и запись должна показывать обе раздельно.
    """
    perimeter = str(model.meta.get("perimeter") or "")
    if not perimeter:
        counts = model.counts()
        perimeter = (f"код репозитория {model.meta.get('repo', '')}: "
                     f"{_plural(counts.get('AO', 0), 'операция', 'операции', 'операций')}, "
                     f"{_plural(counts.get('BBB', 0), 'блок', 'блока', 'блоков')}")
    return Scope(
        in_scope=_as_list(intent.get("in_scope")),
        out_of_scope=_as_list(intent.get("out_of_scope")),
        environment=str(intent.get("environment") or UNSET),
        cohort=str(intent.get("cohort") or UNSET),
        forbidden_effects=_as_list(intent.get("forbidden_effects")),
        analysed_perimeter=perimeter,
    )


def _snapshot(meta: dict[str, Any], decision: GateDecision,
              config_files: list[str]) -> ImplementationSnapshot:
    """Снимок реализации. Конфигурация названа явно, включая её отсутствие."""
    note = ("решение привязано только к коммиту"
            if not config_files else
            "эти файлы входят в снимок наравне с кодом")
    return ImplementationSnapshot(
        ref=decision.implementation_ref,
        commit=str(meta.get("commit", "")),
        commit_date=str(meta.get("commit_date", "")),
        branch=str(meta.get("branch", "")),
        repo=str(meta.get("repo", "")),
        config_files=sorted(config_files),
        config_note=note,
    )


def _restrictions(decision: GateDecision) -> list[str]:
    """Ограничения scope и режима. Понижение режима — тоже ограничение, и его надо назвать."""
    items = list(decision.restrictions)
    if (decision.max_allowed_mode
            and decision.max_allowed_mode != decision.requested_mode):
        items.append(
            f"режим понижен с {decision.requested_mode} до {decision.max_allowed_mode}"
        )
    return items


def _validity(decision: GateDecision, meta: dict[str, Any], generated_at: str) -> Validity:
    """Кто и когда вынес решение и до каких пор оно действует.

    Роль здесь — не человек: решение вычислено детерминированно, и приписывать
    его владельцу или аналитику значит скрыть, что его никто не подписывал.
    """
    by = ("не выносилось" if decision.decision == NO_DECISION
          else "pko gate (детерминированный алгоритм §5.2.3.4)")
    commit = str(meta.get("commit", ""))[:12]
    return Validity(
        decided_by=by,
        decided_at=generated_at,
        bound_to=f"коммит {commit}" if commit else "версия реализации неизвестна",
    )


def _plural(count: int, one: str, few: str, many: str) -> str:
    """«1 операций» в записи допуска читается как небрежность ко всему остальному."""
    tail, tens = count % 10, count % 100
    if tail == 1 and tens != 11:
        form = one
    elif 2 <= tail <= 4 and not 12 <= tens <= 14:
        form = few
    else:
        form = many
    return f"{count} {form}"


def _as_list(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)]
