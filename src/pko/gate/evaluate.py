"""Проверки запуска BASIC: каталог утверждений §5.2 и их вычисление по модели.

Главное правило нормализации §5.2.3.2 реализовано буквально: `PASS` без
доказательства считается отсутствующим результатом, а отсутствующий результат
обязательной проверки — это `FAIL`. Поэтому языковая модель физически не может
выдать допуск: она не участвует в этом модуле.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from pko.checks.test_link import (
    ENFORCEMENT_MARKERS,
    READ_ONLY_KEY,
    confirming_cases,
    junit_case_names,
    skipped_case_names,
)
from pko.checks.validator import ERROR, Issue
from pko.extractors.runner import Extraction
from pko.intent.loader import IntentResult
from pko.model.schema import PkoModel

PASS = "PASS"
FAIL = "FAIL"
NOT_APPLICABLE = "NOT_APPLICABLE"
REQUIRES_FULL_CONTOUR = "REQUIRES_FULL_CONTOUR"

CLASS_BASIC = "BASIC"
CLASS_CONDITIONAL = "CONDITIONAL"

DENY = "DENY"
RESTRICT_SCOPE = "RESTRICT_SCOPE"
LIMIT_MODE = "LIMIT_MODE"


@dataclass
class CheckResult:
    """Строка таблицы проверок Gate Card (§5.1.1): утверждение, статус, основание, ссылка."""

    id: str
    claim: str
    requirement_class: str
    fail_effect: str
    status: str
    basis: str
    evidence: list[str] = field(default_factory=list)
    restriction: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "claim": self.claim,
            "class": self.requirement_class,
            "fail_effect": self.fail_effect,
            "status": self.status,
            "basis": self.basis,
            "evidence": self.evidence,
            "restriction": self.restriction,
        }


@dataclass(frozen=True)
class CheckDefinition:
    id: str
    claim: str
    requirement_class: str
    fail_effect: str
    evaluator: Callable[["GateContext"], tuple[str, str, list[str]]]
    restriction: str = ""


@dataclass
class GateContext:
    model: PkoModel
    extraction: Extraction
    intent: IntentResult
    issues: list[Issue]

    def facts(self, kind: str):
        return self.extraction.by_kind(kind)

    def junit(self):
        return self.extraction.by_kind("TEST_REPORT")


# --- вычислители отдельных проверок ---------------------------------------
def _need_confirmed(ctx: GateContext):
    value = ctx.intent.data.get("confirmed_need_id")
    if value:
        return PASS, f"потребность {value} подтверждена владельцем", [ctx.intent.source]
    return FAIL, "business_intent.yaml не подтверждает потребность клиента", []


def _owner_assigned(ctx: GateContext):
    value = ctx.intent.data.get("business_owner")
    if value:
        return PASS, f"владелец результата: {value}", [ctx.intent.source]
    return FAIL, "владелец клиентского результата не назначен", []


def _outcome_checkable(ctx: GateContext):
    target = ctx.intent.data.get("target_state")
    criteria = ctx.intent.data.get("success_criteria")
    if target and criteria:
        return PASS, "целевое состояние и критерии результата заданы", [ctx.intent.source]
    missing = [n for n, v in (("target_state", target), ("success_criteria", criteria)) if not v]
    return FAIL, "нельзя однозначно определить достижение результата: нет " + ", ".join(missing), []


def _trajectory_recoverable(ctx: GateContext):
    nodes = ctx.facts("GRAPH_NODE")
    routes = ctx.facts("ROUTE")
    if nodes:
        return (
            PASS,
            f"траектория восстановлена: узлов графа {len(nodes)}, переходов {len(ctx.facts('GRAPH_EDGE'))}",
            [f"{f.path}:{f.line}" for f in nodes[:3]],
        )
    if routes:
        return (
            FAIL,
            "найдены точки входа, но граф исполнения не восстановлен — "
            "нельзя показать разрешённую траекторию для каждого входного состояния",
            [f"{f.path}:{f.line}" for f in routes[:3]],
        )
    return FAIL, "точки входа и траектория исполнения не обнаружены", []


def _read_only_proven(ctx: GateContext):
    writes = ctx.facts("SQL_WRITE")
    reads = ctx.facts("SQL_READ")
    if not reads and not writes:
        return NOT_APPLICABLE, "обращений к SQL в коде не обнаружено", []
    if writes:
        return (
            FAIL,
            f"найден SQL, изменяющий данные ({len(writes)} мест) — инвариант «только чтение» нарушен",
            [f"{f.path}:{f.line}" for f in writes[:3]],
        )
    # Та же связь «тест ↔ ограничение», что и в паспорте guardrail: иначе карточка
    # допуска и модель могут разойтись в выводе об одном и том же инварианте.
    confirming = confirming_cases(READ_ONLY_KEY, ctx.extraction)
    if confirming:
        return (
            PASS,
            f"изменяющий SQL не найден, запрет подтверждён тестом: {confirming[0]}",
            [f"{f.path}" for f in ctx.junit()[:1]],
        )
    return (
        FAIL,
        "изменяющий SQL статически не найден, но запрет не доказан негативным тестом "
        "(статический разбор не видит динамический SQL)",
        [f"{f.path}:{f.line}" for f in reads[:3]],
    )


def _limits_present(ctx: GateContext):
    limits = ctx.facts("LIMIT")
    if not limits:
        return FAIL, "числовые ограничения исполнения (таймаут, лимит выдачи) не найдены", []
    kinds = sorted({f.key for f in limits})[:6]
    return PASS, "ограничения найдены: " + ", ".join(kinds), [
        f"{f.path}:{f.line}" for f in limits[:3]
    ]


def _critical_tests(ctx: GateContext):
    reports = ctx.junit()
    if not reports:
        return (
            FAIL,
            "нет готового отчёта pytest (JUnit XML) — критические тесты не подтверждены; "
            "запускать тесты анализируемого проекта PKO не имеет права",
            [],
        )
    total = sum(r.value.get("total", 0) for r in reports)
    failed = sum(r.value.get("failed", 0) for r in reports)
    passed = sum(r.value.get("passed", 0) for r in reports)
    skipped = sum(r.value.get("skipped", 0) for r in reports)
    refs = [r.path for r in reports[:2]]

    if failed:
        return FAIL, f"в отчёте есть падения: {failed} из {total}", refs
    # Отсутствие падений — не то же самое, что выполненная проверка: отчёт, где всё
    # пропущено, формально «без падений», но не подтверждает ни одного сценария.
    if passed == 0:
        return (
            FAIL,
            f"в отчёте нет ни одного пройденного теста: всего {total}, пропущено {skipped}",
            refs,
        )
    tail = f", пропущено {skipped}" if skipped else ""
    return PASS, f"отчёт pytest: пройдено {passed} из {total}, падений нет{tail}", refs


def _negative_scenarios(ctx: GateContext):
    if not ctx.junit():
        return FAIL, "негативные и отказные сценарии не подтверждены отчётом", []
    refs = [r.path for r in ctx.junit()[:1]]
    names = _negative_test_names(ctx)
    if not names:
        skipped_negative = [
            case
            for case in skipped_case_names(ctx.extraction)
            if any(m in case.lower() for m in ENFORCEMENT_MARKERS)
        ]
        if skipped_negative:
            return (
                FAIL,
                f"негативные сценарии в отчёте пропущены и не выполнялись: "
                f"{', '.join(skipped_negative[:3])}",
                refs,
            )
        return FAIL, "в отчёте нет негативных и отказных сценариев", refs
    return PASS, f"пройденных негативных сценариев в отчёте: {len(names)}", refs


def _model_integrity(ctx: GateContext):
    errors = [i for i in ctx.issues if i.level == ERROR]
    if errors:
        return FAIL, f"модель не прошла проверку связности: {errors[0].message}", []
    checked = len(ctx.model.objects)
    return (
        PASS,
        f"валидатор pko.checks.validator: объектов проверено {checked}, ошибок нет",
        [f"pko.json@{str(ctx.model.meta.get('commit', ''))[:8]}"],
    )


def _coverage_sufficient(ctx: GateContext):
    coverage = ctx.model.coverage
    ratio = coverage.ratio
    ref = (
        f"pko.json@{str(ctx.model.meta.get('commit', ''))[:8]}: "
        f"{coverage.files_analyzed}/{coverage.files_total} файлов"
    )
    if ratio >= 0.6:
        return PASS, f"проанализировано {ratio:.0%} файлов", [ref]
    return (
        FAIL,
        f"проанализировано только {ratio:.0%} файлов — вывод не покрывает систему целиком",
        [ref],
    )


def _implementation_ref(ctx: GateContext):
    commit = ctx.model.meta.get("commit")
    if commit and len(str(commit)) >= 7:
        return PASS, f"версия реализации: {str(commit)[:8]}", [str(commit)[:8]]
    return FAIL, "нет точной ссылки на версию реализации", []


# Каталог применимых утверждений BASIC. Последствие FAIL задаётся здесь,
# до выполнения проверки, и не выбирается после получения результата (§5.2.3.1).
CHECKS: tuple[CheckDefinition, ...] = (
    CheckDefinition("CHK-NEED-001", "Потребность клиента подтверждена допустимым источником",
                    CLASS_BASIC, DENY, _need_confirmed),
    CheckDefinition("CHK-OWNER-001", "Назначен владелец клиентского результата",
                    CLASS_BASIC, DENY, _owner_assigned),
    CheckDefinition("CHK-CP-001", "Целевое состояние и критерии результата проверяемы",
                    CLASS_BASIC, DENY, _outcome_checkable),
    CheckDefinition("CHK-AP-001", "Траектория исполнения восстанавливается из реализации",
                    CLASS_BASIC, DENY, _trajectory_recoverable),
    CheckDefinition("CHK-IMPL-001", "Зафиксирована точная версия реализации",
                    CLASS_BASIC, DENY, _implementation_ref),
    CheckDefinition("CHK-MODEL-001", "Модель прошла детерминированную проверку связности",
                    CLASS_BASIC, DENY, _model_integrity),
    CheckDefinition("CHK-GRD-001", "Доступ к данным ограничен чтением и это доказано",
                    CLASS_CONDITIONAL, DENY, _read_only_proven),
    CheckDefinition("CHK-GRD-002", "Заданы числовые ограничения исполнения",
                    CLASS_CONDITIONAL, LIMIT_MODE, _limits_present,
                    restriction="режим ограничен до ASSIST до появления явных лимитов"),
    CheckDefinition("CHK-TEST-001", "Есть протокол критических тестов с фактическим результатом",
                    CLASS_CONDITIONAL, DENY, _critical_tests),
    CheckDefinition("CHK-TEST-002", "Негативные и отказные сценарии проверены",
                    CLASS_CONDITIONAL, RESTRICT_SCOPE, _negative_scenarios,
                    restriction="из scope исключаются сценарии с обработкой ошибок"),
    CheckDefinition("CHK-COV-001", "Анализ покрывает существенную часть репозитория",
                    CLASS_CONDITIONAL, RESTRICT_SCOPE, _coverage_sufficient,
                    restriction="выводы ограничены проанализированной частью кода"),
)


def evaluate_checks(
    model: PkoModel, extraction: Extraction, intent: IntentResult, issues: list[Issue]
) -> list[CheckResult]:
    """Вычислить все применимые проверки. Порядок фиксирован — решение воспроизводимо."""
    ctx = GateContext(model=model, extraction=extraction, intent=intent, issues=issues)
    results: list[CheckResult] = []
    for definition in CHECKS:
        status, basis, evidence = definition.evaluator(ctx)
        # Нормализация §5.2.3.2: PASS без доказательства не является результатом.
        # Правило применяется к любой применимой проверке, а не только к базовой:
        # условная проверка тоже участвует в решении, и её недоказанный PASS так же
        # способен привести к допуску без единой проверяемой ссылки.
        if status == PASS and not evidence:
            status, basis = FAIL, basis + " (нет прямой ссылки на факт)"
        results.append(
            CheckResult(
                id=definition.id,
                claim=definition.claim,
                requirement_class=definition.requirement_class,
                fail_effect=definition.fail_effect,
                status=status,
                basis=basis,
                evidence=evidence,
                restriction=definition.restriction,
            )
        )
    return results


def _negative_test_names(ctx: GateContext) -> list[str]:
    """Пройденные негативные сценарии — выборка шире, чем связь с конкретной политикой."""
    return [
        case
        for case in junit_case_names(ctx.extraction)
        if any(m in case.lower() for m in ENFORCEMENT_MARKERS)
    ]
