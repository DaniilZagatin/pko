"""Нормативные сценарии профилирования §Б.4.

Стандарт прямо говорит: если реализация возвращает иной результат хотя бы для
одного сценария, правила профилирования реализованы неверно. Дополнительно
выполняется обратная проверка — BASIC допустим, только если выполнены все его
условия одновременно.
"""

import unittest

from pko.gate.profile import (
    BASIC,
    FULL,
    ZONE_BASIC,
    ZONE_BLOCKER,
    ZONE_FULL,
    determine_profile,
)


class ProfilingScenarios(unittest.TestCase):
    def test_local_rca_pilot_is_basic(self):
        profile = determine_profile(
            {
                "maturity": "pilot",
                "consequence": "low",
                "requested_mode": "ASSIST",
                "external_effects": "none",
                "scale": "local",
            }
        )
        self.assertEqual(profile.value, BASIC)
        self.assertEqual(profile.zone, ZONE_BASIC)
        self.assertFalse(profile.blocker)
        self.assertEqual(profile.required_machine_level, "BASIC_RECORD")
        self.assertTrue(profile.machine_level_satisfied)
        self.assertEqual(profile.triggers, [])

    def test_limited_launch_with_significant_effect_is_full(self):
        profile = determine_profile(
            {
                "maturity": "limited",
                "consequence": "medium",
                "requested_mode": "CONFIRM",
                "external_effects": ["financial_commitment"],
                "scale": "limited",
            }
        )
        self.assertEqual(profile.value, FULL)
        self.assertFalse(profile.blocker)
        self.assertTrue(
            any("CONFIRM" in t for t in profile.triggers),
            msg="режим CONFIRM при значимом эффекте обязан быть триггером FULL",
        )

    def test_industrial_auto_with_financial_effect_is_full_and_blocked(self):
        profile = determine_profile(
            {
                "maturity": "production",
                "consequence": "high",
                "requested_mode": "AUTO",
                "external_effects": ["financial"],
                "scale": "mass",
            }
        )
        self.assertEqual(profile.value, FULL)
        self.assertEqual(profile.zone, ZONE_BLOCKER)
        self.assertTrue(profile.blocker)
        # Профиль требует ресурсную модель §8.1–8.14, а PKO выпускает
        # `BASIC_RECORD`: заявлять достижение уровня было бы неправдой.
        self.assertEqual(profile.required_machine_level, "FULL_RESOURCE_MODEL")
        self.assertEqual(profile.achieved_machine_level, "BASIC_RECORD")
        self.assertFalse(profile.machine_level_satisfied)

    def test_reverse_rule_basic_requires_all_conditions(self):
        """Обратная проверка: снятие любого условия BASIC переводит решение в FULL."""
        base = {
            "maturity": "pilot",
            "consequence": "low",
            "requested_mode": "ASSIST",
            "external_effects": "none",
            "scale": "local",
        }
        self.assertEqual(determine_profile(base).value, BASIC)

        for field, value in (
            ("requested_mode", "AUTO"),
            ("consequence", "high"),
            ("external_effects", ["legal_obligation"]),
            ("scale", "mass"),
        ):
            with self.subTest(field=field):
                broken = dict(base, **{field: value})
                self.assertEqual(determine_profile(broken).value, FULL)

    def test_missing_intent_defaults_to_pilot_basic(self):
        """Без business_intent профиль не должен становиться мягче, чем пилот."""
        profile = determine_profile(None)
        self.assertEqual(profile.value, BASIC)
        self.assertFalse(profile.blocker)

    def test_typo_in_consequence_does_not_downgrade_risk(self):
        """`hgh` вместо `high` не должно превращать значимый запуск в BASIC."""
        profile = determine_profile(
            {
                "maturity": "limited",
                "consequence": "hgh",
                "requested_mode": "CONFIRM",
                "external_effects": "none",
                "scale": "local",
            }
        )
        self.assertEqual(profile.value, FULL)
        self.assertEqual(profile.zone, ZONE_BLOCKER)
        self.assertTrue(profile.blocker)
        self.assertTrue(any("нераспознанные" in t for t in profile.triggers))

    def test_unknown_maturity_is_treated_as_industrial(self):
        profile = determine_profile(
            {
                "maturity": "prod-like",
                "consequence": "low",
                "requested_mode": "ASSIST",
                "external_effects": "none",
                "scale": "local",
            }
        )
        self.assertEqual(profile.value, FULL)
        self.assertEqual(profile.zone, ZONE_FULL)

    def test_unknown_mode_is_a_trigger(self):
        profile = determine_profile(
            {
                "maturity": "pilot",
                "consequence": "low",
                "requested_mode": "SEMI_AUTO",
                "external_effects": "none",
                "scale": "local",
            }
        )
        self.assertEqual(profile.value, FULL)
        self.assertTrue(any("SEMI_AUTO" in t for t in profile.triggers))


if __name__ == "__main__":
    unittest.main()
