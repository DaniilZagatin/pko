"""Сквозной прогон по тестовому репозиторию: содержание модели, детерминизм, diff."""

import json
import unittest

from fixture_support import EXAMPLE_INTENT, JUNIT_ALL_SKIPPED, JUNIT_OK, ensure_fixture
from pko.diff.engine import ADDED, diff_models
from pko.git.repo import GitRepo
from pko.history.selector import select_versions
from pko.pipeline import analyze_version

JUNIT = JUNIT_OK


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = GitRepo(ensure_fixture())
        cls.versions = select_versions(cls.repo, "master", max_versions=2)

    def _analyze(self, version, junit=None, intent=None):
        return analyze_version(
            repo=self.repo, version=version, repo_name="mini_repo",
            branch="master", junit_path=junit, intent_path=intent,
        )

    def test_two_versions_selected(self):
        self.assertEqual([v.label for v in self.versions], ["v1", "current"])

    def test_model_recovers_objects_from_code(self):
        analysis = self._analyze(self.versions[-1])
        model = analysis.model
        counts = model.counts()
        self.assertGreaterEqual(counts["BBB"], 3)
        # AO требует подтверждённого исполнения. Импорты LangGraph,
        # SQLAlchemy и boto3 остаются зависимостями, но не раздувают этот
        # счётчик; в фикстуре явно наблюдается SQL-чтение.
        self.assertGreaterEqual(counts["AO"], 1)
        self.assertGreaterEqual(counts["GUARDRAIL"], 3)

        process = model.by_kind("PROCESS")[0]
        self.assertIn("plan", process.get_text("Правила сборки траектории"))
        self.assertIn("POST /tasks", process.get_text("Условия запуска"))

    def test_secrets_are_not_extracted(self):
        analysis = self._analyze(self.versions[-1])
        dump = analysis.model.to_json()
        self.assertNotIn("секрет-не-должен-попасть", dump)

    def test_evidence_points_to_real_files(self):
        analysis = self._analyze(self.versions[-1])
        files = set(self.repo.files(self.versions[-1].sha))
        for obj in analysis.model.objects:
            for ev in obj.all_evidence():
                if ev.path.endswith(".xml") or ev.path.startswith("/"):
                    continue
                with self.subTest(path=ev.path):
                    self.assertTrue(
                        ev.path in files or any(f.startswith(ev.path + "/") for f in files),
                        msg=f"{obj.id}: доказательство ссылается на {ev.path}",
                    )

    def test_intent_is_read_from_repository(self):
        analysis = self._analyze(self.versions[-1])
        self.assertTrue(analysis.intent.present)
        self.assertEqual(analysis.intent.data["confirmed_need_id"], "NEED-HR-001")

    def test_gate_denies_without_test_report(self):
        analysis = self._analyze(self.versions[-1])
        self.assertEqual(analysis.decision.decision, "DENY")
        self.assertIn("CHK-TEST-001", analysis.decision.blocking)

    def test_gate_allows_with_test_report(self):
        analysis = self._analyze(self.versions[-1], junit=JUNIT)
        self.assertEqual(analysis.decision.decision, "ALLOW")
        self.assertEqual([c.id for c in analysis.checks if c.status == "FAIL"], [])

    def test_all_skipped_report_proves_nothing(self):
        """Отчёт, где всё пропущено, не должен читаться как «падений нет»."""
        analysis = self._analyze(self.versions[-1], junit=JUNIT_ALL_SKIPPED)

        by_id = {c.id: c for c in analysis.checks}
        self.assertEqual(by_id["CHK-TEST-001"].status, "FAIL",
                         msg="пропущенные тесты не подтверждают критические сценарии")
        self.assertEqual(by_id["CHK-TEST-002"].status, "FAIL")
        self.assertEqual(by_id["CHK-GRD-001"].status, "FAIL",
                         msg="скипнутый негативный тест не доказывает запрет на запись")
        self.assertIn("пропущ", by_id["CHK-TEST-002"].basis.lower())

        read_only = next(
            g for g in analysis.model.by_kind("GUARDRAIL")
            if g.links.get("limit_key") == ["read_only"]
        )
        self.assertNotEqual(read_only.fields["Подтверждён тестом"].origin, "VERIFIED")
        self.assertEqual(analysis.decision.decision, "DENY")

    def test_passed_and_skipped_are_distinguished_in_report(self):
        analysis = self._analyze(self.versions[-1], junit=JUNIT_ALL_SKIPPED)
        report = analysis.extraction.by_kind("TEST_REPORT")[0]
        self.assertEqual(report.value["passed"], 0)
        self.assertEqual(report.value["skipped"], 4)
        self.assertTrue(all(c["outcome"] == "skipped" for c in report.value["cases"]))

    def test_read_only_verdict_matches_guardrail_passport(self):
        """Карточка допуска и паспорт ограничения обязаны говорить одно и то же."""
        for junit in (None, JUNIT):
            with self.subTest(junit=bool(junit)):
                analysis = self._analyze(self.versions[-1], junit=junit)
                read_only = [
                    g for g in analysis.model.by_kind("GUARDRAIL")
                    if g.links.get("limit_key") == ["read_only"]
                ]
                self.assertEqual(len(read_only), 1)
                confirmed_in_model = read_only[0].fields["Подтверждён тестом"].origin == "VERIFIED"
                check = next(c for c in analysis.checks if c.id == "CHK-GRD-001")
                self.assertEqual(
                    confirmed_in_model,
                    check.status == "PASS",
                    msg="модель и Gate разошлись в выводе об инварианте «только чтение»",
                )

    def test_untested_guardrail_keeps_warning(self):
        """Ограничение без связанного теста остаётся неподтверждённым и с предупреждением."""
        analysis = self._analyze(self.versions[-1], junit=JUNIT)
        # Тест называется test_timeout_limit_applied и по строгому правилу связи не
        # подтверждает лимит выдачи: в имени нет части ключа «rows».
        # Ключ политики — «компонент|семейство»: родственные параметры одного
        # компонента образуют одно ограничение, а одноимённые из разных мест
        # остаются разными.
        rows = [
            g for g in analysis.model.by_kind("GUARDRAIL")
            if g.links.get("limit_key") == ["backend/src/config|rows"]
        ]
        self.assertTrue(rows, "в фикстуре есть ограничение db_max_rows")
        self.assertNotEqual(rows[0].fields["Подтверждён тестом"].origin, "VERIFIED")
        self.assertTrue(
            any(i.code == "GUARDRAIL_NOT_TESTED" and i.object_id == rows[0].id
                for i in analysis.issues)
        )

    def test_same_effect_from_two_packages_is_one_operation(self):
        """Одно обращение к базе из разных каталогов — одна операция, а не шесть.

        Раньше в ключ входил пакет, и управленческий слой превращался в
        перечень мест вызова с одинаковыми названиями.
        """
        analysis = self._analyze(self.versions[-1])
        operations = analysis.model.by_kind("AO")
        names = [o.name for o in operations]
        self.assertEqual(len(names), len(set(names)),
                         msg=f"повторяющиеся операции: {names}")
        for obj in operations:
            with self.subTest(op=obj.name):
                self.assertIn("Компоненты", obj.fields,
                              msg="слияние не должно скрывать, где операция живёт")

    def test_read_and_write_are_never_merged(self):
        from pko.assemble.candidates import build_candidates
        from pko.assemble.heuristic import build_model
        from pko.extractors.base import Fact
        from pko.extractors.runner import Extraction

        facts = [
            Fact(kind="SQL_READ", key="sql", value="SELECT", path="a/one.py", line=1),
            Fact(kind="SQL_READ", key="sql", value="SELECT", path="b/two.py", line=1),
            Fact(kind="SQL_WRITE", key="sql", value="INSERT", path="a/one.py", line=5),
        ]
        extraction = Extraction(facts=facts)
        model = build_model(extraction=extraction,
                            candidates=build_candidates(extraction),
                            meta={"commit": "x", "repo": "t"})
        operations = model.by_kind("AO")
        self.assertEqual(len(operations), 2, msg=[o.name for o in operations])
        merged = next(o for o in operations if "Чтение" in o.name)
        self.assertEqual(merged.get_text("Количество мест вызова"), "2")

    def test_related_limits_of_one_component_form_one_guardrail(self):
        """Четыре таймаута одного сервера — одна политика, а не четыре."""
        from pko.assemble.candidates import build_candidates
        from pko.assemble.heuristic import build_model
        from pko.extractors.base import Fact
        from pko.extractors.runner import Extraction

        facts = [
            Fact(kind="LIMIT", key="timeout", value=60, path="app/settings/base.py", line=1),
            Fact(kind="LIMIT", key="timeout_keep_alive", value=5,
                 path="app/settings/base.py", line=2),
            Fact(kind="LIMIT", key="ws_ping_timeout", value=20,
                 path="app/settings/base.py", line=3),
            # Таймаут другого компонента защищает другой участок исполнения.
            Fact(kind="LIMIT", key="timeout", value=30,
                 path="app/services/nlp/encoder.py", line=4),
        ]
        extraction = Extraction(facts=facts)
        model = build_model(extraction=extraction,
                            candidates=build_candidates(extraction),
                            meta={"commit": "x", "repo": "t"})
        guardrails = model.by_kind("GUARDRAIL")
        self.assertEqual(len(guardrails), 2, msg=[g.name for g in guardrails])
        server = next(g for g in guardrails if "settings" in g.get_text("Точка применения"))
        self.assertIn("timeout_keep_alive", server.get_text("Значение"))
        self.assertIn("ws_ping_timeout", server.get_text("Значение"))

    def test_migration_writes_keep_deny_but_are_named(self):
        """Вердикт прежний, но читателю видно, что все изменения — в миграциях."""
        from pko.assemble.candidates import build_candidates
        from pko.assemble.heuristic import build_model
        from pko.extractors.base import Fact
        from pko.extractors.runner import Extraction
        from pko.gate.evaluate import evaluate_checks
        from pko.intent.loader import IntentResult

        facts = [Fact(kind="SQL_WRITE", key="sql", value="INSERT INTO nodes",
                      path="backend/alembic/versions/6fb.py", line=157)]
        extraction = Extraction(facts=facts)
        model = build_model(extraction=extraction,
                            candidates=build_candidates(extraction),
                            meta={"commit": "x", "repo": "t"})
        checks = evaluate_checks(model, extraction, IntentResult(), [])
        read_only = next(c for c in checks if c.id == "CHK-GRD-001")

        self.assertEqual(read_only.status, "FAIL", msg="изменение данных остаётся нарушением")
        self.assertIn("в миграциях схемы", read_only.basis)
        self.assertTrue(any("в миграциях схемы" in g for g in model.gaps))

    def test_external_intent_path_is_portable(self):
        """Абсолютный путь оператора не должен попадать в отчёт.

        В карточке он печатался в колонке «Ссылка на факт» вместе с домашним
        каталогом и корпоративным логином, а читателю бесполезен: на другой
        машине такого пути нет.
        """
        analysis = self._analyze(self.versions[-1], intent=str(EXAMPLE_INTENT))
        self.assertRegex(
            analysis.intent.source,
            r"^external/business_intent-[0-9a-f]{12}\.yaml$",
        )

        paths = {
            ev.path for obj in analysis.model.objects
            for ev in obj.all_evidence() if ev.path
        }
        absolute = sorted(p for p in paths if p.startswith("/"))
        self.assertEqual(absolute, [], msg=f"абсолютные пути в доказательствах: {absolute}")
        self.assertIn(analysis.intent.source, paths)
        # Внешний источник не должен ломать проверку связности.
        self.assertFalse(analysis.has_errors, msg=[i.render() for i in analysis.issues])

    def test_analysis_is_deterministic(self):
        first = self._analyze(self.versions[-1]).model.to_json()
        second = self._analyze(self.versions[-1]).model.to_json()
        self.assertEqual(json.loads(first), json.loads(second))

    def test_diff_detects_new_objects(self):
        old = self._analyze(self.versions[0]).model
        new = self._analyze(self.versions[-1]).model
        diff = diff_models(old, new)
        self.assertGreater(diff.summary()[ADDED], 0)
        self.assertGreater(diff.counts_right["BBB"], diff.counts_left["BBB"])
        # Потребность не появилась заново — она была и осталась одна.
        self.assertEqual(diff.counts_left["NEED"], diff.counts_right["NEED"])

    def test_unchanged_guardrail_is_not_reported_as_changed(self):
        """Добавление новых лимитов не должно «менять» неизменившийся sql_timeout.

        Номер GRD-NNN позиционный: во второй версии фикстуры появляются новые
        ограничения, и сопоставление по номеру раньше подставляло чужую пару.
        """
        old = self._analyze(self.versions[0]).model
        new = self._analyze(self.versions[-1]).model
        diff = diff_models(old, new)

        def find(model):
            return next(
                g for g in model.by_kind("GUARDRAIL")
                if g.links.get("limit_key") == ["backend/src/config|timeout"]
            )

        old_grd, new_grd = find(old), find(new)
        self.assertEqual(old_grd.get_text("Значение"), new_grd.get_text("Значение"))

        entry = next(o for o in diff.objects if o.id == new_grd.id and o.kind == "GUARDRAIL")
        self.assertEqual(
            entry.status, "SAME",
            msg=f"sql_timeout не менялся, но помечен как {entry.status}: "
                f"{[c.label for c in entry.changes]}",
        )
        self.assertNotIn(
            new_grd.id,
            [o.id for o in diff.objects if o.status == ADDED],
            msg="неизменившееся ограничение не может быть новым",
        )

    def test_unchanged_bbb_is_not_reported_as_changed(self):
        """Появление новых пакетов сдвигает BBB-NNN — сопоставление должно это переживать.

        В первой версии единственный блок — «Приём запросов пользователя» (BBB-001).
        Во второй перед ним по алфавиту встаёт пакет agent, и BBB-001 — уже другой блок.
        """
        old = self._analyze(self.versions[0]).model
        new = self._analyze(self.versions[-1]).model
        diff = diff_models(old, new)

        def api_block(model):
            return next(
                b for b in model.by_kind("BBB")
                if b.links.get("package") == ["backend/src/api"]
            )

        old_api, new_api = api_block(old), api_block(new)
        self.assertNotEqual(
            old_api.id, new_api.id, msg="нумерация блоков должна была сдвинуться"
        )

        entry = next(o for o in diff.objects if o.kind == "BBB" and o.id == new_api.id)
        self.assertEqual(
            entry.status, "SAME",
            msg=f"блок приёма запросов не менялся, но помечен как {entry.status}: "
                f"{[c.label for c in entry.changes]}",
        )

    def test_atomic_operations_have_stable_identity(self):
        analysis = self._analyze(self.versions[-1])
        keys = [o.links.get("stable_key") for o in analysis.model.by_kind("AO")]
        self.assertTrue(all(keys), msg="у каждой операции должен быть устойчивый ключ")
        self.assertEqual(len(keys), len({k[0] for k in keys}), msg="ключи операций уникальны")

    def test_relative_intent_override_does_not_break_evidence(self):
        """`--intent` с относительным путём не должен превращаться в отказ.

        Путь файла — это не свойство анализируемой системы: раньше относительный
        путь искали внутри коммита, не находили и роняли проверку связности.
        """
        rel = EXAMPLE_INTENT
        self.assertTrue(rel.exists(), "образец intent лежит в репозитории PKO")

        baseline = self._analyze(self.versions[-1], junit=JUNIT)
        for path in (rel, rel.resolve()):
            with self.subTest(path=str(path)):
                analysis = self._analyze(self.versions[-1], junit=JUNIT, intent=path)
                broken = [i for i in analysis.issues if i.code == "EVIDENCE_PATH_MISSING"]
                self.assertEqual(broken, [], msg=f"{[i.render() for i in broken]}")
                self.assertEqual(analysis.decision.decision, baseline.decision.decision)

    def test_junit_is_not_attached_to_other_versions(self):
        """Отчёт о тестах — доказательство для того коммита, на котором он получен."""
        with_report = self._analyze(self.versions[-1], junit=JUNIT)
        without = self._analyze(self.versions[0])
        self.assertTrue(with_report.extraction.by_kind("TEST_REPORT"))
        self.assertEqual(without.extraction.by_kind("TEST_REPORT"), [])

    def test_guardrail_names_are_distinguishable(self):
        """Три «Ограничения времени исполнения» подряд делали отчёт неоднозначным."""
        analysis = self._analyze(self.versions[-1])
        names = [g.name for g in analysis.model.by_kind("GUARDRAIL")]
        self.assertEqual(len(names), len(set(names)), msg=f"дубли имён: {names}")

    def test_first_version_has_no_confirmed_intent(self):
        analysis = self._analyze(self.versions[0])
        self.assertFalse(analysis.intent.present)
        self.assertTrue(any("business_intent" in g for g in analysis.model.gaps))

    def test_missing_intent_is_draft_not_denial(self):
        """Нет ручного входа — решение не выносится; это не отказ в допуске."""
        analysis = self._analyze(self.versions[0])
        self.assertEqual(analysis.decision.decision, "NO_DECISION")
        self.assertIsNone(analysis.decision.max_allowed_mode)


if __name__ == "__main__":
    unittest.main()
