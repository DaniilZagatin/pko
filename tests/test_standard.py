"""Каталог требований стандарта, готовность к FULL и честность уровня соответствия.

Главная проверяемая вещь здесь не в формате, а в утверждении: отчёт не должен
объявлять достигнутым машинный уровень, которого PKO не выпускает.
"""

import unittest

from pko.gate.profile import BASIC, FULL, determine_profile
from pko.model import readiness
from pko.standard import catalog
from pko.standard.coverage_doc import render


class MachineLevelHonestyTest(unittest.TestCase):
    """Требуемый и достигнутый уровень §8.0 — разные величины."""

    def _profile(self, **intent):
        return determine_profile(intent)

    def test_pilot_requires_and_reaches_basic_record(self):
        profile = self._profile(maturity="pilot", consequence="low", requested_mode="ASSIST")
        self.assertEqual(profile.value, BASIC)
        self.assertEqual(profile.required_machine_level, "BASIC_RECORD")
        self.assertEqual(profile.achieved_machine_level, "BASIC_RECORD")
        self.assertTrue(profile.machine_level_satisfied)

    def test_full_profile_does_not_claim_resource_model(self):
        """Раньше карточка печатала `FULL_RESOURCE_MODEL` — уровень, которого нет.

        Профиль FULL означает, что требования §8.1–8.14 применяются, а не что
        PKO их выполнил: ресурсной модели с `ResourceRef`, конвертами и
        событиями он не выпускает.
        """
        profile = self._profile(maturity="production", consequence="medium",
                                requested_mode="AUTO", external_effects=["financial"])
        self.assertEqual(profile.value, FULL)
        self.assertEqual(profile.required_machine_level, "FULL_RESOURCE_MODEL")
        self.assertEqual(profile.achieved_machine_level, "BASIC_RECORD")
        self.assertFalse(profile.machine_level_satisfied)

    def test_dict_carries_both_levels(self):
        data = self._profile(maturity="pilot", consequence="low").to_dict()
        self.assertIn("required_machine_level", data)
        self.assertIn("achieved_machine_level", data)
        self.assertNotIn("machine_level", data,
                         msg="одно поле для двух разных величин снова вводило бы в заблуждение")


class CatalogTest(unittest.TestCase):
    def test_every_requirement_is_well_formed(self):
        seen = set()
        for requirement in catalog.REQUIREMENTS:
            with self.subTest(requirement=requirement.id):
                self.assertNotIn(requirement.id, seen, msg="идентификаторы уникальны")
                seen.add(requirement.id)
                self.assertIn(requirement.state, catalog.STATES)
                self.assertIn(requirement.profile, (catalog.BASIC, catalog.FULL, catalog.BOTH))
                self.assertTrue(requirement.section)
                self.assertTrue(requirement.area)

    def test_partial_requirements_name_their_limit(self):
        """«Частично» без названной границы — это обещание, а не оценка."""
        for requirement in catalog.REQUIREMENTS:
            if requirement.state == catalog.PARTIAL:
                with self.subTest(requirement=requirement.id):
                    self.assertTrue(requirement.limitation,
                                    msg="у частичной проверки должна быть названа граница")

    def test_checked_requirements_name_their_source(self):
        for requirement in catalog.REQUIREMENTS:
            if requirement.state == catalog.CHECKED:
                with self.subTest(requirement=requirement.id):
                    self.assertTrue(requirement.source)

    def test_basic_profile_does_not_pull_in_industrial_requirements(self):
        basic = catalog.requirements_for(catalog.BASIC)
        self.assertTrue(basic)
        self.assertFalse([r for r in basic if r.profile == catalog.FULL],
                         msg="машинный контракт не добавляет к BASIC скрытых требований §6")

    def test_runtime_and_unimplemented_are_different_states(self):
        """`NOT_CHECKED` — работа, `NEEDS_RUNTIME` — граница подхода."""
        states = {r.state for r in catalog.REQUIREMENTS}
        self.assertIn(catalog.NOT_CHECKED, states)
        self.assertIn(catalog.NEEDS_RUNTIME, states)
        trace = next(r for r in catalog.REQUIREMENTS if r.id == "FULL-TRACE")
        self.assertEqual(trace.state, catalog.NEEDS_RUNTIME)


class ReadinessTest(unittest.TestCase):
    """Готовность к §6 отвечает на другой вопрос, чем допуск."""

    def test_full_profile_is_not_ready_and_says_why(self):
        result = readiness.assess(catalog.FULL, {"BBB": 4, "AO": 4, "GUARDRAIL": 7})
        self.assertEqual(result.status, "NOT_READY")
        self.assertTrue(result.required)
        self.assertTrue(result.blocking)
        # Оценка готовности не должна читаться как отказ в допуске.
        self.assertIn("не отказ в допуске", result.summary)

    def test_basic_profile_reports_not_required(self):
        result = readiness.assess(catalog.BASIC)
        self.assertEqual(result.status, "NOT_REQUIRED")
        self.assertFalse(result.required)
        self.assertIn("не применяются", result.summary)

    def test_every_area_names_a_basis_and_a_next_step(self):
        """Область без основания — это ярлык, по которому нечего делать."""
        for area in readiness.assess(catalog.FULL).areas:
            with self.subTest(area=area.name):
                self.assertTrue(area.basis)
                self.assertNotEqual(area.basis, "—")
                if area.state != readiness.READY:
                    self.assertTrue(area.next_action)

    def test_no_readiness_percentage_is_produced(self):
        """Процент готовности создаёт ложную точность там, где половина областей не проверяется."""
        data = readiness.assess(catalog.FULL).to_dict()
        self.assertNotIn("percent", data)
        self.assertNotIn("score", data)
        self.assertIn("blocking_requirements", data)

    def test_areas_are_ordered_worst_first(self):
        areas = readiness.assess(catalog.FULL).areas
        severity = [readiness._SEVERITY[a.state] for a in areas]
        self.assertEqual(severity, sorted(severity, reverse=True))


class CoverageDocTest(unittest.TestCase):
    def test_document_matches_the_catalogue(self):
        """Документ собирается из кода: разойтись они не могут."""
        text = render()
        for requirement in catalog.REQUIREMENTS:
            with self.subTest(requirement=requirement.id):
                self.assertIn(requirement.id, text)
        self.assertIn("make coverage-doc", text)
        self.assertIn("runtime_poc.md", text)


if __name__ == "__main__":
    unittest.main()
