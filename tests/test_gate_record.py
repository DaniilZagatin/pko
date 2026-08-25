"""Запись допуска `BASIC_RECORD` — контракт §8.0.1.

Проверяется полнота полей и одна вещь важнее полноты: запись не должна
подставлять за владельца то, чего он не подтверждал.
"""

import json
import unittest

from fixture_support import ensure_fixture
from pko.gate.record import INVALIDATION, UNSET, build_record
from pko.intent.loader import record_gaps
from pko.git.repo import GitRepo
from pko.history.selector import select_versions
from pko.pipeline import analyze_version
from pko.render.gate_card import render_gate_card

GENERATED_AT = "2026-08-12 12:00"

# Поля таблицы §8.0.1. Отсутствие любого делает запись невосстановимой.
CONTRACT_FIELDS = (
    "record_version", "profile", "decision_boundary", "purpose_and_result",
    "business_owner", "scope", "requested_mode", "implementation_ref", "checks",
    "restrictions", "decision", "validity",
)


class RecordContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo = GitRepo(ensure_fixture())
        version = select_versions(repo, "master", max_versions=1)[0]
        cls.analysis = analyze_version(
            repo=repo, version=version, repo_name="mini_repo", branch="master"
        )
        cls.record = build_record(
            cls.analysis.model, cls.analysis.checks, cls.analysis.decision,
            cls.analysis.intent.data, GENERATED_AT,
            config_files=["config/app.yaml"],
            record_gaps=cls.analysis.intent.record_gaps,
        )
        cls.data = cls.record.to_dict(GENERATED_AT)

    def test_all_contract_fields_are_restorable(self):
        for name in CONTRACT_FIELDS:
            with self.subTest(field=name):
                self.assertIn(name, self.data)

    def test_record_version_is_bound_to_the_commit(self):
        """Версия записи без коммита не отличает две проверки разного кода."""
        commit = str(self.analysis.model.meta["commit"])[:8]
        self.assertIn(commit, self.data["record_version"])

    def test_scope_separates_the_declared_boundary_from_the_analysed_one(self):
        """Периметр разбора — не разрешённый scope; смешивать их значит расширять допуск."""
        scope = self.data["scope"]
        self.assertIn("analysed_perimeter", scope)
        self.assertNotIn(scope["analysed_perimeter"], scope["in_scope"])

    def test_unset_owner_fields_are_not_invented(self):
        empty = build_record(self.analysis.model, self.analysis.checks,
                             self.analysis.decision, {}, GENERATED_AT)
        self.assertEqual(empty.business_owner, UNSET)
        self.assertEqual(empty.decision_boundary, UNSET,
                         msg="END_TO_END_PROCESS по умолчанию расширял допуск на весь процесс")
        self.assertEqual(empty.purpose_and_result["stopped_outcome"], UNSET)
        self.assertEqual(empty.scope.in_scope, [])

    def test_validity_names_what_invalidates_the_record(self):
        validity = self.data["validity"]
        self.assertTrue(validity["decided_at"])
        self.assertIn("коммит", validity["bound_to"])
        self.assertEqual(validity["invalidated_by"], list(INVALIDATION))

    def test_decision_is_not_attributed_to_a_person(self):
        """Решение вычислено алгоритмом; подпись роли создала бы ложную ответственность."""
        self.assertIn("pko gate", self.data["validity"]["decided_by"])

    def test_configuration_is_part_of_the_implementation_snapshot(self):
        impl = self.data["implementation"]
        self.assertEqual(impl["config_files"], ["config/app.yaml"])
        self.assertTrue(impl["config_note"])

    def test_missing_configuration_is_stated_not_omitted(self):
        record = build_record(self.analysis.model, self.analysis.checks,
                              self.analysis.decision, {}, GENERATED_AT, config_files=[])
        self.assertEqual(record.implementation.config_files, [])
        self.assertIn("только к коммиту", record.implementation.config_note)

    def test_mode_downgrade_is_recorded_as_a_restriction(self):
        decision = self.analysis.decision
        original = decision.max_allowed_mode
        try:
            decision.max_allowed_mode = "ASSIST"
            decision.requested_mode = "AUTO"
            record = build_record(self.analysis.model, self.analysis.checks,
                                  decision, {}, GENERATED_AT)
        finally:
            decision.max_allowed_mode = original
        self.assertTrue(any("режим понижен" in r for r in record.restrictions))

    def test_record_gaps_travel_with_the_record(self):
        """Незаполненные поля §8.0.1 — часть записи, а не примечание к прогону."""
        self.assertEqual(self.data["record_gaps"], self.analysis.intent.record_gaps)


class ConfigSnapshotTest(unittest.TestCase):
    """Что попадает в снимок реализации как «существенная конфигурация» (§8.0.1).

    Отбор шёл по маркеру `SETTING file:`, который выпускает только
    `pko.extractors.deps` и только для YAML/TOML/INI. Файл `config/agent.json`,
    задающий режим, лимиты и allowlist, в снимок не попадал вовсе — при том,
    что запись утверждала привязку решения к конфигурации.
    """

    POLICY_JSON = json.dumps({
        "mode": "CONFIRM",
        "timeout": 30,
        "allowed_hosts": ["internal.example"],
    }, ensure_ascii=False)

    def _snapshot(self, files: dict[str, str], extra_facts=()):
        from types import SimpleNamespace

        from pko.cli import _config_files
        from pko.extractors import policy_specs
        from pko.extractors.runner import Extraction
        from test_contracts import FakeTree

        facts = list(policy_specs.extract(FakeTree(files))) + list(extra_facts)
        return _config_files(SimpleNamespace(extraction=Extraction(facts=facts)))

    def test_json_policy_file_lands_in_the_snapshot(self):
        self.assertEqual(self._snapshot({"config/agent.json": self.POLICY_JSON}),
                         ["config/agent.json"])

    def test_yaml_policy_file_lands_in_the_snapshot(self):
        files = {"config/agent.yaml": "timeout: 30\nallowed_hosts:\n  - internal.example\n"}
        self.assertEqual(self._snapshot(files), ["config/agent.yaml"])

    def test_one_file_is_listed_once(self):
        """Файл, давший три наблюдения, — это один файл конфигурации, а не три."""
        snapshot = self._snapshot({"config/agent.json": self.POLICY_JSON})
        self.assertEqual(len(snapshot), len(set(snapshot)))

    def test_limits_found_in_source_code_are_not_configuration(self):
        """`timeout=30` в `.py` покрыт коммитом; называть его конфигурацией — ложь."""
        from pko.extractors.base import Fact

        code_limit = Fact(kind="LIMIT", key="timeout", value=30, path="src/app.py",
                          line=10, basis="таймаут в коде", category="CONTROL",
                          action="declare", mechanism="limit")
        self.assertEqual(self._snapshot({}, [code_limit]), [])

    def test_owner_intent_is_never_part_of_the_implementation(self):
        from pko.extractors.base import Fact

        intent_setting = Fact(kind="SETTING", key="file:business_intent.yaml", value=[],
                              path="business_intent.yaml", line=1, basis="намерение")
        self.assertEqual(self._snapshot({}, [intent_setting]), [])

    def test_tool_manifest_is_substantial_configuration(self):
        """Состав инструментов задаёт, что система вправе делать, и меняется без кода."""
        from pko.extractors import contracts
        from test_contracts import FakeTree, TOOLS

        self.assertEqual(
            self._snapshot({}, contracts.extract(FakeTree({"agent/tools.json": TOOLS}))),
            ["agent/tools.json"],
        )

    def test_interface_specification_is_not_configuration(self):
        """OpenAPI описывает контракт, но сам по себе поведения не меняет."""
        from pko.extractors import contracts
        from test_contracts import FakeTree, OPENAPI

        spec = contracts.extract(FakeTree({"docs/openapi.yaml": OPENAPI}))
        self.assertTrue(spec, "спецификация должна разбираться")
        self.assertEqual(self._snapshot({}, spec), [])


class CardMatchesRecordTest(unittest.TestCase):
    """Markdown и JSON — два вида одной записи. Расхождение между ними хуже отсутствия одного."""

    @classmethod
    def setUpClass(cls):
        repo = GitRepo(ensure_fixture())
        version = select_versions(repo, "master", max_versions=1)[0]
        cls.analysis = analyze_version(
            repo=repo, version=version, repo_name="mini_repo", branch="master"
        )
        cls.record = build_record(
            cls.analysis.model, cls.analysis.checks, cls.analysis.decision,
            cls.analysis.intent.data, GENERATED_AT,
            record_gaps=cls.analysis.intent.record_gaps,
        )
        cls.card = render_gate_card(
            cls.analysis.model, cls.analysis.checks, cls.analysis.decision, cls.record,
        )

    def test_card_prints_the_same_record_version(self):
        self.assertIn(self.record.record_version, self.card)

    def test_card_has_a_scope_section(self):
        self.assertIn("Заявленный scope — допуск не выдан", self.card)
        self.assertIn("Периметр анализа", self.card)

    def test_card_states_the_validity_conditions(self):
        self.assertIn("Срок действия", self.card)
        for condition in INVALIDATION:
            with self.subTest(condition=condition):
                self.assertIn(condition, self.card)

    def test_card_marks_unconfirmed_fields_instead_of_filling_them(self):
        card = self._card({})
        self.assertIn(f"**{UNSET}**", card)
        self.assertNotIn("весь проанализированный код", card,
                         msg="периметр разбора не является разрешённым scope")

    def test_pipes_in_owner_text_do_not_break_the_table(self):
        intent = dict(self.analysis.intent.data)
        intent["business_owner"] = "Роль | подразделение"
        card = self._card(intent)
        self.assertIn(r"Роль \| подразделение", card)

    def test_multiline_owner_fields_do_not_break_the_table(self):
        """Блочный скаляр `|` разрешён разбором намерения и обязан пережить таблицу.

        Физический перенос внутри ячейки обрывал таблицу: остаток текста
        становился абзацем, а следующие поля записи допуска пропадали из
        таблицы вовсе — читатель видел карточку без владельца и без границ.
        """
        from pko.util.yamlmini import loads

        intent = loads(
            "business_meaning: |\n"
            "  Сотрудник получает ответ.\n"
            "  Без посредников.\n"
            "business_owner: |-\n"
            "  Иванова А.А.\n"
            "  Владелец продукта\n"
        )
        self.assertIn("\n", intent["business_meaning"], "разбор сохраняет переносы")

        card = self._card(intent)
        table = [ln for ln in card.splitlines() if ln.startswith("| Назначение")]
        self.assertEqual(len(table), 1)
        self.assertIn("Сотрудник получает ответ.<br>Без посредников.", table[0])

        # Раздел «Идентификация и граница» обязан остаться целой таблицей:
        # каждая её строка начинается и заканчивается вертикальной чертой.
        rows = self._section_rows(card, "## 1. Идентификация и граница")
        self.assertTrue(all(r.endswith("|") for r in rows), rows)
        self.assertTrue(any(r.startswith("| Бизнес-владелец") for r in rows))

    def test_pipes_and_newlines_together_stay_escaped(self):
        intent = {"business_owner": "Роль | подразделение\nвторая строка"}
        card = self._card(intent)
        row = next(ln for ln in card.splitlines() if ln.startswith("| Бизнес-владелец"))
        self.assertIn(r"Роль \| подразделение<br>вторая строка", row)

    def _card(self, intent) -> str:
        """Карточка от намерения: запись собирается ровно так же, как в CLI."""
        record = build_record(self.analysis.model, self.analysis.checks,
                              self.analysis.decision, intent, GENERATED_AT,
                              record_gaps=record_gaps(intent))
        return render_gate_card(self.analysis.model, self.analysis.checks,
                                self.analysis.decision, record)

    @staticmethod
    def _section_rows(card: str, heading: str) -> list[str]:
        lines = card.splitlines()
        start = lines.index(heading)
        rows = []
        for line in lines[start + 1:]:
            if line.startswith("## "):
                break
            if line.startswith("|"):
                rows.append(line)
        return rows

    def test_sections_are_numbered_without_gaps(self):
        numbers = [int(line[3]) for line in self.card.splitlines()
                   if line.startswith("## ") and line[3].isdigit()]
        self.assertEqual(numbers, sorted(numbers))
        self.assertEqual(len(numbers), len(set(numbers)), "повторный номер раздела")


if __name__ == "__main__":
    unittest.main()
