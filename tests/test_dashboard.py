"""`index.html` — единственная страница, с которой начинают чтение.

Проверяется не вёрстка, а три обещания: страница открывается с диска без сети,
её можно пройти с клавиатуры и она не расходится с JSON-аудитами рядом.
"""

import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from fixture_support import ensure_fixture, JUNIT_OK
from pko.cli import _report_links, main
from pko.gate.record import build_record
from pko.git.repo import GitRepo
from pko.history.selector import select_versions
from pko.model import readiness as readiness_mod
from pko.pipeline import analyze_version
from pko.render.dashboard import render_dashboard

# Ссылка наружу превращает автономный файл в зависимость от сети и от того,
# что чужой домен ещё жив. В корпоративном контуре это просто пустая страница.
EXTERNAL = re.compile(r"""(?:src|href)\s*=\s*["'](?!#)(https?:|//|data:)""", re.I)


class DashboardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo = GitRepo(ensure_fixture())
        version = select_versions(repo, "master", max_versions=1)[0]
        cls.analysis = analyze_version(
            repo=repo, version=version, repo_name="mini_repo", branch="master"
        )
        cls.html = render_dashboard(
            cls.analysis.model, cls.analysis.checks, cls.analysis.decision,
            cls.analysis.readiness,
            links={"basic_gate.json": "запись допуска"},
        )

    def test_opens_from_disk_without_network(self):
        self.assertIsNone(EXTERNAL.search(self.html),
                          msg="внешние ресурсы делают отчёт нечитаемым в закрытом контуре")
        self.assertNotIn("<script src", self.html)

    def test_disclosure_is_keyboard_reachable(self):
        """Раскрытие сделано на `<details>`: он фокусируется и открывается с клавиатуры.

        Обработчик клика на `<div>` выглядит так же, но с клавиатуры недоступен.
        """
        self.assertIn("<details", self.html)
        self.assertIn("<summary", self.html)
        self.assertNotIn("onclick=", self.html)

    def test_scope_section_names_the_boundary_of_admission(self):
        """Вердикт без границ читается шире, чем он есть."""
        record = build_record(self.analysis.model, self.analysis.checks,
                              self.analysis.decision, self.analysis.intent.data,
                              "2026-08-12 12:00")
        html = render_dashboard(self.analysis.model, self.analysis.checks,
                                self.analysis.decision, self.analysis.readiness,
                                record=record)
        self.assertIn("Граница решения — допуск не выдан", html)
        self.assertIn(record.validity.bound_to, html)
        self.assertIn("это граница разбора, а не разрешённый scope", html)

    def test_unset_scope_is_described_fail_closed(self):
        """Пустая граница не означает «всё разрешено», даже в старой записи."""
        record = build_record(self.analysis.model, self.analysis.checks,
                              self.analysis.decision, {}, "2026-08-12 12:00")
        html = render_dashboard(self.analysis.model, self.analysis.checks,
                                self.analysis.decision, self.analysis.readiness,
                                record=record)
        self.assertIn("ни одна операция не считается разрешённой", html)
        self.assertIn("это не разрешение ни на процесс, ни на компонент", html)
        self.assertNotIn("вердикт относится ко всему", html)

    def test_scope_section_is_absent_without_a_record(self):
        self.assertNotIn("Граница решения — допуск не выдан", self.html)

    def test_llm_overview_is_marked_as_written_by_a_model(self):
        """Объяснение модели не должно читаться как факт, найденный в коде."""
        html = render_dashboard(self.analysis.model, self.analysis.checks,
                                self.analysis.decision, self.analysis.readiness,
                                overview="Система отвечает на вопросы по данным.",
                                overview_source="llm")
        self.assertIn("сформулировала языковая модель", html)

    def test_template_overview_says_no_model_was_involved(self):
        html = render_dashboard(self.analysis.model, self.analysis.checks,
                                self.analysis.decision, self.analysis.readiness,
                                overview="Система отвечает на вопросы по данным.",
                                overview_source="template")
        self.assertIn("без языковой модели", html)
        self.assertNotIn("сформулировала языковая модель", html)

    def test_verdict_comes_first(self):
        """Читатель должен увидеть решение раньше, чем состав объектов."""
        verdict = self.html.index('class="verdict')
        for later in ("Как система работает", "Подробности и аудиты"):
            self.assertLess(verdict, self.html.index(later))

    def test_failed_checks_appear_as_tasks(self):
        failed = [c for c in self.analysis.checks if c.status == "FAIL"]
        for check in failed:
            with self.subTest(check=check.id):
                self.assertIn(check.claim, self.html)

    def test_blocking_checks_are_distinguished_from_the_rest(self):
        failed = [c for c in self.analysis.checks if c.status == "FAIL"]
        if not failed:
            self.skipTest("на фикстуре нет провалов — различать нечего")
        blocking = set(self.analysis.decision.blocking or [])
        if blocking:
            self.assertIn("блокирует допуск", self.html)
        else:
            self.assertNotIn("блокирует допуск", self.html)

    def test_readiness_is_shown_separately_from_admission(self):
        """Готовность к §6 и допуск — разные вопросы; смешать их значит соврать дважды."""
        self.assertIn("Готовность к промышленному контуру", self.html)
        self.assertIn(self.analysis.readiness.summary, self.html)

    def test_no_percentage_of_readiness(self):
        self.assertNotRegex(self.html, r"готовност\w*[^<]{0,40}\d+\s*%")

    def test_gaps_section_is_present_even_without_gaps(self):
        """Отчёт без раздела о непокрытом читается как отчёт без пробелов."""
        empty = render_dashboard(self.analysis.model, [], self.analysis.decision,
                                 self.analysis.readiness)
        self.assertIn("Что мешает запуску", empty)
        self.assertIn("Ни одна применимая проверка не провалена", empty)

    def test_links_point_only_to_written_files(self):
        """Ссылка на ненаписанный файл хуже отсутствия ссылки."""
        files = {"a": ("basic_gate.json", "{}"), "b": ("taxonomy.html", "")}
        links = _report_links(files)
        self.assertEqual(set(links), {"basic_gate.json", "taxonomy.html"})
        self.assertNotIn("passports.html", links)

    def test_comparison_and_gate_cards_get_a_purpose(self):
        files = {"c": ("comparison_v1_v2.html", ""), "g": ("gate_card_v2.md", "")}
        links = _report_links(files)
        self.assertEqual(set(links), {"comparison_v1_v2.html", "gate_card_v2.md"})
        self.assertTrue(all(links.values()), "у каждой ссылки должно быть объяснение")

    def test_hostile_object_names_do_not_break_the_page(self):
        model = self.analysis.model
        target = model.by_kind("BBB")[0] if model.by_kind("BBB") else model.objects[0]
        original = target.name
        try:
            target.name = 'Блок </style><script>alert(1)</script>'
            html = render_dashboard(model, self.analysis.checks, self.analysis.decision,
                                    self.analysis.readiness)
        finally:
            target.name = original
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


class FullProfileDashboardTest(unittest.TestCase):
    """Профиль FULL: страница обязана сказать, что требования применяются, а не выполнены."""

    @classmethod
    def setUpClass(cls):
        repo = GitRepo(ensure_fixture())
        version = select_versions(repo, "master", max_versions=1)[0]
        cls.analysis = analyze_version(
            repo=repo, version=version, repo_name="mini_repo", branch="master",
            intent_path=None,
        )

    def test_full_readiness_lists_blocking_requirements(self):
        readiness = readiness_mod.assess("FULL", self.analysis.model.counts())
        html = render_dashboard(self.analysis.model, self.analysis.checks,
                                self.analysis.decision, readiness)
        self.assertEqual(readiness.status, "NOT_READY")
        for area in readiness.areas:
            with self.subTest(area=area.name):
                self.assertIn(area.name, html)

    def test_runtime_areas_are_not_called_unfinished_work(self):
        """`NEEDS_RUNTIME` — граница подхода; называть её недоделкой значит обещать лишнее."""
        readiness = readiness_mod.assess("FULL", self.analysis.model.counts())
        runtime = [a for a in readiness.areas if a.state == readiness_mod.NEEDS_RUNTIME]
        self.assertTrue(runtime, "часть требований §6 статически не проверяется")
        html = render_dashboard(self.analysis.model, self.analysis.checks,
                                self.analysis.decision, readiness)
        self.assertIn("нужен исполняющий контур", html.lower())


class OutputSetTest(unittest.TestCase):
    """Полный прогон: набор файлов и согласованность `index.html` с аудитами."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        out = Path(cls._tmp.name) / "out"
        buffer = io.StringIO()
        argv = ["analyze", "--repo-path", str(ensure_fixture()), "--branch", "master",
                "--max-versions", "2", "--out", str(out), "--junit", str(JUNIT_OK)]
        stdout, sys.stdout = sys.stdout, buffer
        try:
            cls.code = main(argv)
        finally:
            sys.stdout = stdout
        cls.out = out

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _read(self, name):
        return (self.out / name).read_text(encoding="utf-8")

    def test_run_succeeds_and_writes_the_expected_set(self):
        self.assertEqual(self.code, 0)
        for name in ("index.html", "basic_gate.json", "full_readiness.json",
                     "standard_coverage.json", "passports.html", "taxonomy.html"):
            with self.subTest(name=name):
                self.assertTrue((self.out / name).exists(), f"нет файла {name}")

    def test_every_link_on_the_dashboard_resolves(self):
        html = self._read("index.html")
        for name in re.findall(r'href="([^"#][^"]*)"', html):
            with self.subTest(link=name):
                self.assertTrue((self.out / name).exists(), f"битая ссылка: {name}")

    def test_dashboard_verdict_matches_the_gate_record(self):
        gate = json.loads(self._read("basic_gate.json"))
        html = self._read("index.html")
        decision = gate["decision"]["decision"]
        expected = {"ADMITTED": "Допуск выдан", "ADMITTED_WITH_RESTRICTIONS": "Допуск с ограничениями",
                    "NOT_ADMITTED": "Допуск не выдан"}.get(decision)
        if expected:
            self.assertIn(expected, html)

    def test_gate_record_does_not_claim_an_unreached_machine_level(self):
        profile = json.loads(self._read("basic_gate.json"))["profile"]
        self.assertEqual(profile["achieved_machine_level"], "BASIC_RECORD")
        if profile["profile"] == "FULL":
            self.assertFalse(profile["machine_level_satisfied"])

    def test_failed_checks_are_the_same_in_json_and_on_the_page(self):
        gate = json.loads(self._read("basic_gate.json"))
        html = self._read("index.html")
        for check in gate["checks"]:
            if check["status"] == "FAIL":
                with self.subTest(check=check["id"]):
                    self.assertIn(check["claim"], html)

    def test_readiness_json_and_page_agree(self):
        data = json.loads(self._read("full_readiness.json"))
        html = self._read("index.html")
        self.assertIn(data["summary"], html)
        for area in data["areas"]:
            with self.subTest(area=area["area"]):
                self.assertIn(area["area"], html)

    def test_coverage_json_admits_runtime_gap_even_for_basic(self):
        """§8.0.2 требует записи о каждом запуске и в BASIC — её PKO не производит."""
        data = json.loads(self._read("standard_coverage.json"))
        states = {r["state"] for r in data["requirements"]}
        self.assertIn("NEEDS_RUNTIME", states)

    def test_coverage_json_says_how_much_it_omitted(self):
        """Отфильтрованный список без счётчика неотличим от полного каталога."""
        data = json.loads(self._read("standard_coverage.json"))
        self.assertEqual(data["applicable_requirements"], len(data["requirements"]))
        self.assertEqual(
            data["total_requirements"],
            data["applicable_requirements"] + len(data["omitted_for_profile"]),
        )

    def test_reports_are_standalone(self):
        for name in ("index.html", "passports.html", "taxonomy.html"):
            with self.subTest(name=name):
                self.assertIsNone(EXTERNAL.search(self._read(name)))

    def test_external_junit_parent_is_absent_from_every_report(self):
        """`--junit /home/user/...` не раскрывает машину оператора в комплекте."""
        private_parent = str(JUNIT_OK.resolve().parent)
        for path in self.out.iterdir():
            if not path.is_file():
                continue
            with self.subTest(report=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(private_parent, text)

        semantic = json.loads(self._read("semantic_facts.json"))
        reports = [
            fact for version in semantic["versions"] for fact in version["facts"]
            if fact["kind"] == "TEST_REPORT"
        ]
        self.assertTrue(reports, "входной JUnit должен остаться в машинном аудите")
        for fact in reports:
            self.assertRegex(fact["path"], r"^external/junit_ok-[0-9a-f]{12}\.xml$")

        # На исторической версии тот же ID используется в gap, но сам report
        # не присваивается коммиту, на котором тесты не запускались.
        historical_gaps = semantic["versions"][0]["gaps"]
        self.assertTrue(any("external/junit_ok-" in gap for gap in historical_gaps))
        self.assertTrue(all(private_parent not in gap for gap in historical_gaps))


if __name__ == "__main__":
    unittest.main()
