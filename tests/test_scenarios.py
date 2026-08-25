"""Сценарии допуска целиком: BASIC, обязательный FULL и неполная запись.

Каждый сценарий проверяет не одну функцию, а согласие всего комплекта: решение,
запись §8.0.1, оценка готовности и страница читателя должны говорить одно и то
же. Расхождение между ними — самая дорогая ошибка PKO: читатель поверит тому
файлу, который открыл первым.
"""

import tempfile
import textwrap
import unittest
from pathlib import Path

from fixture_support import JUNIT_OK, ensure_fixture
from pko.gate.decide import ALLOW, ALLOW_WITH_RESTRICTIONS, NO_DECISION, REQUIRE_FULL_CONTOUR
from pko.gate.record import UNSET, build_record
from pko.git.repo import GitRepo
from pko.history.selector import select_versions
from pko.pipeline import analyze_version
from pko.render.dashboard import render_dashboard
from pko.render.gate_card import render_gate_card

GENERATED_AT = "2026-08-12 12:00"

BASIC_INTENT = """
confirmed_need_id: NEED-HR-001
business_owner: Иванова А.А.
client: HR-аналитик
business_meaning: Ответ по данным без посредников
target_state: Пользователь получил проверяемый ответ
stopped_state: Пользователь получил отказ с причиной
success_criteria: Ответ содержит ссылку на источник
maturity: pilot
consequence: low
requested_mode: ASSIST
decision_boundary: END_TO_END_PROCESS
in_scope: Чтение данных и синтез ответа
out_of_scope: Изменение данных
forbidden_effects: Платежи, письма клиентам
external_effects: none
environment: пилотный стенд
cohort: 20 сотрудников HR
owner_confirmed_at: 2026-08-01
"""

# Тот же процесс, но заявленный как промышленный с финансовым эффектом:
# по матрице §0.2.1 это FULL, и допуск по BASIC-контуру выдавать нельзя.
FULL_INTENT = BASIC_INTENT.replace("maturity: pilot", "maturity: production") \
    .replace("consequence: low", "consequence: high") \
    .replace("requested_mode: ASSIST", "requested_mode: AUTO") \
    .replace("external_effects: none", "external_effects: financial")

# Клиентский результат и низкорисковый профиль заполнены, но владелец не назвал
# область полномочий. До исправления такой вход считался complete и при зелёных
# тестах мог дать ALLOW без ответа на вопрос «что именно разрешено».
INTENT_WITHOUT_AUTHORIZATION = """
confirmed_need_id: NEED-HR-001
business_owner: Иванова А.А.
target_state: Пользователь получил проверяемый ответ
success_criteria: Ответ содержит ссылку на источник
maturity: pilot
consequence: low
requested_mode: ASSIST
scale: local
external_effects: none
"""


class ScenarioCase(unittest.TestCase):
    """Общая обвязка: разобрать фикстуру с заданным намерением."""

    @classmethod
    def setUpClass(cls):
        cls.repo = GitRepo(ensure_fixture())
        cls.version = select_versions(cls.repo, "master", max_versions=1)[0]
        cls._tmp = tempfile.TemporaryDirectory()

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def analyze(self, intent_text: str | None, junit=JUNIT_OK):
        path = None
        if intent_text is not None:
            path = Path(self._tmp.name) / f"intent_{abs(hash(intent_text))}.yaml"
            path.write_text(textwrap.dedent(intent_text).lstrip(), encoding="utf-8")
        return analyze_version(
            repo=self.repo, version=self.version, repo_name="mini_repo", branch="master",
            junit_path=str(junit) if junit else None,
            intent_path=str(path) if path else None,
        )

    def artefacts(self, analysis):
        record = build_record(analysis.model, analysis.checks, analysis.decision,
                              analysis.intent.data, GENERATED_AT,
                              record_gaps=analysis.intent.record_gaps)
        card = render_gate_card(analysis.model, analysis.checks, analysis.decision, record)
        page = render_dashboard(analysis.model, analysis.checks, analysis.decision,
                                analysis.readiness, record=record)
        return record, card, page


class BasicScenarioTest(ScenarioCase):
    """Пилот с низкими последствиями: запись выпускается и уровень достигнут."""

    def setUp(self):
        self.analysis = self.analyze(BASIC_INTENT)
        self.record, self.card, self.page = self.artefacts(self.analysis)

    def test_profile_is_basic_and_the_level_matches(self):
        profile = self.analysis.profile
        self.assertEqual(profile.value, "BASIC")
        self.assertTrue(profile.machine_level_satisfied)

    def test_decision_is_a_verdict_not_a_draft(self):
        self.assertNotEqual(self.analysis.decision.decision, NO_DECISION)

    def test_owner_fields_are_filled_from_the_intent(self):
        self.assertNotEqual(self.record.business_owner, UNSET)
        self.assertEqual(self.record.decision_boundary, "END_TO_END_PROCESS")
        self.assertEqual(self.record.scope.forbidden_effects, ["Платежи, письма клиентам"])
        self.assertEqual(self.record.gaps, [], msg="все поля §8.0.1 заполнены")

    def test_readiness_is_not_required_but_still_reported(self):
        """Пилоту полезно видеть, чего не хватит для промышленного запуска."""
        readiness = self.analysis.readiness
        self.assertEqual(readiness.status, "NOT_REQUIRED")
        self.assertTrue(readiness.areas)
        self.assertIn("Готовность к промышленному контуру", self.page)

    def test_card_does_not_mark_owner_fields_as_missing(self):
        self.assertNotIn(f"**{UNSET}**", self.card)


class MandatoryFullScenarioTest(ScenarioCase):
    """Промышленный запуск с финансовым эффектом: BASIC-контура недостаточно."""

    def setUp(self):
        self.analysis = self.analyze(FULL_INTENT)
        self.record, self.card, self.page = self.artefacts(self.analysis)

    def test_profile_is_full(self):
        self.assertEqual(self.analysis.profile.value, "FULL")
        self.assertTrue(self.analysis.profile.triggers)

    def test_admission_is_not_granted_on_the_basic_contour(self):
        self.assertNotIn(self.analysis.decision.decision, (ALLOW, ALLOW_WITH_RESTRICTIONS))
        self.assertIsNone(self.analysis.decision.max_allowed_mode)

    def test_required_level_is_not_the_achieved_one(self):
        """Главное утверждение всей итерации: PKO не выдаёт ресурсную модель §8.1–8.14."""
        profile = self.analysis.profile
        self.assertEqual(profile.required_machine_level, "FULL_RESOURCE_MODEL")
        self.assertEqual(profile.achieved_machine_level, "BASIC_RECORD")
        self.assertFalse(profile.machine_level_satisfied)

    def test_card_prints_both_levels(self):
        self.assertIn("требуется `FULL_RESOURCE_MODEL`", self.card)
        self.assertIn("выпущено `BASIC_RECORD`", self.card)

    def test_readiness_is_required_and_blocking_is_listed(self):
        readiness = self.analysis.readiness
        self.assertEqual(readiness.status, "NOT_READY")
        self.assertTrue(readiness.blocking)

    def test_page_does_not_call_the_process_admitted(self):
        for phrase in ("Запуск разрешён", "Допуск выдан"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.page)


class MissingAuthorizationBoundaryTest(ScenarioCase):
    """Зелёные проверки не создают полномочия, которых владелец не объявил."""

    def setUp(self):
        self.analysis = self.analyze(INTENT_WITHOUT_AUTHORIZATION)
        self.record, self.card, self.page = self.artefacts(self.analysis)

    def test_result_fields_without_scope_cannot_produce_allow(self):
        self.assertEqual(self.analysis.decision.decision, NO_DECISION)
        self.assertIsNone(self.analysis.decision.max_allowed_mode)
        self.assertFalse(self.analysis.intent.complete)
        self.assertEqual(
            {"decision_boundary", "in_scope", "forbidden_effects"},
            set(self.analysis.intent.missing),
        )

    def test_report_explains_that_missing_scope_grants_nothing(self):
        self.assertNotIn("Запуск разрешён", self.page)
        self.assertNotIn("вердикт относится ко всему", self.page)
        self.assertIn("ни одна операция не считается разрешённой", self.page)
        self.assertIn("полномочия не выдаются", self.page)
        self.assertIn("допуск не выдан", self.page.lower())


class MissingRuntimeControlsTest(ScenarioCase):
    """Контроль исполнения отсутствует, и отчёт обязан назвать это границей подхода."""

    def setUp(self):
        self.analysis = self.analyze(FULL_INTENT)
        _, _, self.page = self.artefacts(self.analysis)

    def test_runtime_areas_are_named_as_such(self):
        states = {a.state for a in self.analysis.readiness.areas}
        self.assertIn("NEEDS_RUNTIME", states)
        self.assertIn("нужен исполняющий контур", self.page)

    def test_policy_enforcement_is_never_claimed(self):
        """Ни один найденный guardrail не доказывает, что ограничение применяется."""
        for phrase in ("ограничения применяются", "policy layer работает",
                       "журнал исполнения ведётся"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, self.page)


# Намерение без полей, которые спрашивают проверки с последствием DENY:
# по такому входу проверять клиентский результат не по чему.
BARE_INTENT = """
maturity: pilot
consequence: low
requested_mode: ASSIST
"""


class IncompleteInputTest(ScenarioCase):
    """Неполное покрытие входов: вердикт не выносится, но черновик выпускается."""

    def setUp(self):
        self.analysis = self.analyze(BARE_INTENT, junit=None)
        self.record, self.card, self.page = self.artefacts(self.analysis)

    def test_incomplete_intent_means_no_verdict(self):
        """Отсутствие подтверждённых полей — не отказ в допуске, а отсутствие решения."""
        self.assertEqual(self.analysis.decision.decision, NO_DECISION)
        self.assertIn("Решение не выносилось", self.page)
        self.assertTrue(self.analysis.intent.missing)

    def test_record_is_structurally_complete_but_empty(self):
        """Пустая запись должна выглядеть пустой, а не заполненной по умолчанию."""
        self.assertEqual(self.record.business_owner, UNSET)
        self.assertEqual(self.record.decision_boundary, UNSET)
        self.assertEqual(self.record.scope.in_scope, [])
        self.assertTrue(self.record.gaps, "незаполненные поля §8.0.1 должны быть перечислены")

    def test_card_lists_what_the_owner_has_to_fill(self):
        self.assertIn("Чего не хватает в самой записи", self.card)
        self.assertIn(f"**{UNSET}**", self.card)

    def test_draft_is_not_presented_as_a_refusal(self):
        """Черновик и отказ — разные сигналы: их смешение стоит владельцу лишней работы."""
        self.assertNotIn("Запуск не разрешён", self.page)

    def test_missing_test_report_is_a_named_gap_not_a_pass(self):
        """PKO не имеет права запускать тесты проекта: без отчёта проверка проваливается."""
        tests_check = next(c for c in self.analysis.checks if c.id == "CHK-TEST-001")
        self.assertEqual(tests_check.status, "FAIL")
        self.assertTrue(tests_check.basis)
        self.assertFalse(tests_check.evidence,
                         msg="PASS без доказательства не является результатом (§5.2.3.2)")


if __name__ == "__main__":
    unittest.main()
