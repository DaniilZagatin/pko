"""Решение Gate — детерминированная функция, реализующая алгоритм §5.2.3.4.

Языковая модель здесь не участвует ни в каком виде. Одинаковый набор строк
проверок при одинаковой версии реализации всегда даёт одинаковое решение, как
требует §5.2.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pko.gate.evaluate import (
    DENY,
    FAIL,
    LIMIT_MODE,
    NOT_APPLICABLE,
    PASS,
    REQUIRES_FULL_CONTOUR,
    RESTRICT_SCOPE,
    CLASS_BASIC,
    CheckResult,
)
from pko.gate.profile import Profile

ALLOW = "ALLOW"
ALLOW_WITH_RESTRICTIONS = "ALLOW_WITH_RESTRICTIONS"
DECISION_DENY = "DENY"
REQUIRE_FULL_CONTOUR = "REQUIRE_FULL_CONTOUR"

# Не решение Gate, а его отсутствие: без подтверждённого бизнес-намерения проверять
# клиентский результат и границы автономности не по чему (§4.0). Отдельный статус
# нужен, чтобы черновик не читался как отказ в допуске — это разные сигналы.
NO_DECISION = "NO_DECISION"

# Порядок строгости решений (§5.2.3.3).
PRECEDENCE = (DECISION_DENY, REQUIRE_FULL_CONTOUR, ALLOW_WITH_RESTRICTIONS, ALLOW)

MODE_ORDER = ("HUMAN_ONLY", "ASSIST", "CONFIRM", "AUTO")


@dataclass
class GateDecision:
    decision: str
    requested_mode: str
    max_allowed_mode: str | None
    restrictions: list[str] = field(default_factory=list)
    blocking: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)
    implementation_ref: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "requested_mode": self.requested_mode,
            "max_allowed_mode": self.max_allowed_mode,
            "restrictions": self.restrictions,
            "blocking_check_ids": self.blocking,
            "reasons": self.reasons,
            "profile": self.profile,
            "implementation_ref": self.implementation_ref,
        }


def decide(
    results: list[CheckResult],
    profile: Profile,
    requested_mode: str,
    implementation_ref: str,
    intent_present: bool,
    draft_reason: str = "business_intent.yaml не найден",
) -> GateDecision:
    """Вычислить решение по совокупности формальных результатов."""
    requested_mode = (requested_mode or "ASSIST").upper()
    # Неизвестный режим нельзя объявлять разрешённым: непонятно, что именно
    # разрешается. Верхняя граница в таком случае не устанавливается.
    known_mode = requested_mode in MODE_ORDER
    base = GateDecision(
        decision=ALLOW,
        requested_mode=requested_mode,
        max_allowed_mode=requested_mode if known_mode else None,
        profile=profile.to_dict(),
        implementation_ref=implementation_ref,
    )
    if not known_mode:
        base.reasons.append(
            f"Запрошен неизвестный режим исполнения {requested_mode!r}: "
            f"допустимы {', '.join(MODE_ORDER)}"
        )

    # Без подтверждённого бизнес-намерения Gate не выносит вердикт вовсе:
    # проверить клиентский результат и границы автономности не по чему (§4.0).
    # Это не отказ в допуске, а его отсутствие — отсюда отдельный статус.
    if not intent_present:
        base.decision = NO_DECISION
        base.max_allowed_mode = None
        base.reasons.append(f"Черновик: {draft_reason} — решение о допуске не выносится")
        base.blocking = [r.id for r in results if r.status == FAIL and r.fail_effect == DENY]
        return base

    if profile.blocker:
        base.decision = DECISION_DENY
        base.max_allowed_mode = None
        base.reasons.append(
            "Зона блокера запуска по матрице 0.2: профиль FULL не снимает блокер, "
            "нужно снизить scope или режим"
        )
        return base

    failures = [r for r in results if r.status == FAIL]
    deny_hits = [r for r in failures if r.fail_effect == DENY or r.requirement_class == CLASS_BASIC]
    full_contour = [r for r in results if r.status == REQUIRES_FULL_CONTOUR]

    if deny_hits:
        base.decision = DECISION_DENY
        base.max_allowed_mode = None
        base.blocking = [r.id for r in deny_hits]
        base.reasons = [f"{r.id}: {r.basis}" for r in deny_hits]
        return base

    if profile.value == "FULL" or full_contour:
        base.decision = REQUIRE_FULL_CONTOUR
        base.max_allowed_mode = None
        base.blocking = [r.id for r in full_contour]
        base.reasons = (
            [f"{r.id}: {r.basis}" for r in full_contour]
            or ["Профиль FULL: требуется полный проверяемый контур"]
        )
        if profile.triggers:
            base.reasons.extend(f"Триггер FULL: {t}" for t in profile.triggers)
        return base

    if not known_mode:
        # Профиль уже переведён в FULL нераспознанным входом, но подстрахуемся:
        # выдать допуск на режим, которого нет в стандарте, нельзя ни при каких проверках.
        base.decision = REQUIRE_FULL_CONTOUR
        base.max_allowed_mode = None
        return base

    restricting = [r for r in failures if r.fail_effect in {RESTRICT_SCOPE, LIMIT_MODE}]
    if restricting:
        base.decision = ALLOW_WITH_RESTRICTIONS
        base.blocking = [r.id for r in restricting]
        base.restrictions = [r.restriction or r.claim for r in restricting]
        base.reasons = [f"{r.id}: {r.basis}" for r in restricting]
        if any(r.fail_effect == LIMIT_MODE for r in restricting):
            base.max_allowed_mode = _limit_mode(requested_mode)
        return base

    base.reasons.append("Все применимые обязательные проверки пройдены")
    return base


def _limit_mode(requested: str) -> str:
    """LIMIT_MODE понижает режим на ступень и никогда не поднимает его выше запрошенного.

    Без верхнего ограничения `HUMAN_ONLY` (индекс 0) превращался в `ASSIST`: провал
    условной проверки выдавал владельцу больше автономии, чем он просил.
    """
    try:
        idx = MODE_ORDER.index(requested)
    except ValueError:
        return "ASSIST"
    return MODE_ORDER[min(idx, max(1, idx - 1))]


def summarize(results: list[CheckResult]) -> dict[str, int]:
    return {
        PASS: sum(1 for r in results if r.status == PASS),
        FAIL: sum(1 for r in results if r.status == FAIL),
        NOT_APPLICABLE: sum(1 for r in results if r.status == NOT_APPLICABLE),
        REQUIRES_FULL_CONTOUR: sum(1 for r in results if r.status == REQUIRES_FULL_CONTOUR),
    }
