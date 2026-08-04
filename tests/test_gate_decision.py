"""Решение Gate: приоритеты §5.2.3.3 и воспроизведение вердикта примера §Б.1.3."""

import unittest

from pko.cli import _exit_code
from pko.gate.decide import (
    ALLOW,
    ALLOW_WITH_RESTRICTIONS,
    DECISION_DENY,
    MODE_ORDER,
    NO_DECISION,
    REQUIRE_FULL_CONTOUR,
    decide,
)
from pko.gate.evaluate import (
    CLASS_BASIC,
    CLASS_CONDITIONAL,
    DENY,
    FAIL,
    LIMIT_MODE,
    PASS,
    RESTRICT_SCOPE,
    CheckResult,
)
from pko.gate.profile import determine_profile

PILOT = {
    "maturity": "pilot",
    "consequence": "low",
    "requested_mode": "ASSIST",
    "external_effects": "none",
    "scale": "local",
}


def result(check_id, status, cls=CLASS_CONDITIONAL, effect=DENY, restriction=""):
    return CheckResult(
        id=check_id, claim=check_id, requirement_class=cls, fail_effect=effect,
        status=status, basis="тест", evidence=["ref"], restriction=restriction,
    )


class GateDecisionTest(unittest.TestCase):
    def setUp(self):
        self.profile = determine_profile(PILOT)

    def _decide(self, results, mode="ASSIST", intent=True):
        return decide(results, self.profile, mode, "repo@abc1234", intent_present=intent)

    def test_all_pass_gives_allow(self):
        d = self._decide([result("CHK-1", PASS), result("CHK-2", PASS)])
        self.assertEqual(d.decision, ALLOW)
        self.assertEqual(d.max_allowed_mode, "ASSIST")

    def test_basic_fail_denies(self):
        d = self._decide([result("CHK-1", FAIL, cls=CLASS_BASIC), result("CHK-2", PASS)])
        self.assertEqual(d.decision, DECISION_DENY)
        self.assertIn("CHK-1", d.blocking)
        self.assertIsNone(d.max_allowed_mode)

    def test_restrictions_give_allow_with_restrictions(self):
        d = self._decide(
            [
                result("CHK-1", PASS),
                result("CHK-2", FAIL, effect=RESTRICT_SCOPE, restriction="без обработки ошибок"),
            ]
        )
        self.assertEqual(d.decision, ALLOW_WITH_RESTRICTIONS)
        self.assertEqual(d.restrictions, ["без обработки ошибок"])

    def test_limit_mode_lowers_mode(self):
        d = self._decide([result("CHK-2", FAIL, effect=LIMIT_MODE)], mode="CONFIRM")
        self.assertEqual(d.decision, ALLOW_WITH_RESTRICTIONS)
        self.assertEqual(d.max_allowed_mode, "ASSIST")

    def test_limit_mode_never_raises_autonomy(self):
        """Понижение режима не может выдать больше автономии, чем просил владелец."""
        for requested, expected in (
            ("HUMAN_ONLY", "HUMAN_ONLY"),
            ("ASSIST", "ASSIST"),
            ("CONFIRM", "ASSIST"),
            ("AUTO", "CONFIRM"),
        ):
            with self.subTest(requested=requested):
                d = self._decide([result("CHK-2", FAIL, effect=LIMIT_MODE)], mode=requested)
                self.assertEqual(d.max_allowed_mode, expected)
                self.assertLessEqual(
                    MODE_ORDER.index(d.max_allowed_mode),
                    MODE_ORDER.index(requested),
                    msg="разрешённый режим оказался выше запрошенного",
                )

    def test_deny_wins_over_restrictions(self):
        """Более строгое решение всегда имеет приоритет (§5.2.3.3)."""
        d = self._decide(
            [result("CHK-1", FAIL, cls=CLASS_BASIC), result("CHK-2", FAIL, effect=RESTRICT_SCOPE)]
        )
        self.assertEqual(d.decision, DECISION_DENY)

    def test_full_profile_requires_full_contour(self):
        profile = determine_profile(dict(PILOT, requested_mode="AUTO"))
        d = decide([result("CHK-1", PASS)], profile, "AUTO", "repo@abc", intent_present=True)
        self.assertEqual(d.decision, REQUIRE_FULL_CONTOUR)

    def test_blocker_zone_denies_even_with_passes(self):
        profile = determine_profile(dict(PILOT, consequence="high", maturity="limited"))
        d = decide([result("CHK-1", PASS)], profile, "ASSIST", "repo@abc", intent_present=True)
        self.assertEqual(d.decision, DECISION_DENY)

    def test_without_intent_no_verdict(self):
        """Нет бизнес-намерения — решение не выносится, и это не отказ (§4.0)."""
        d = self._decide([result("CHK-1", PASS)], intent=False)
        self.assertEqual(d.decision, NO_DECISION)
        self.assertNotEqual(d.decision, DECISION_DENY)
        self.assertIsNone(d.max_allowed_mode)
        self.assertTrue(any("черновик" in r.lower() for r in d.reasons))

    def test_decision_is_reproducible(self):
        results = [result("CHK-1", PASS), result("CHK-2", FAIL, effect=RESTRICT_SCOPE)]
        first = self._decide(results).to_dict()
        second = self._decide(results).to_dict()
        self.assertEqual(first, second)


class ExitCodeTest(unittest.TestCase):
    """Контракт кодов возврата: ноль означает выданный допуск и только его."""

    def test_all_decisions_are_mapped(self):
        self.assertEqual(_exit_code(ALLOW), 0)
        self.assertEqual(_exit_code(ALLOW_WITH_RESTRICTIONS), 0)
        self.assertEqual(_exit_code(DECISION_DENY), 3)
        self.assertEqual(_exit_code(REQUIRE_FULL_CONTOUR), 3)
        self.assertEqual(_exit_code(NO_DECISION), 4)

    def test_unknown_decision_is_not_success(self):
        self.assertNotEqual(_exit_code("СОВЕРШЕННО НОВЫЙ СТАТУС"), 0)


if __name__ == "__main__":
    unittest.main()
