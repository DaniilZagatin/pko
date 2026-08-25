"""Отчёты: в раздел «Связи» попадают только ссылки на объекты."""

import unittest

from fixture_support import ensure_fixture
from pko.git.repo import GitRepo
from pko.history.selector import select_versions
from pko.model.schema import REFERENCE_LINKS
from pko.pipeline import analyze_version
from pko.render.comparison import render_comparison
from pko.render.passports import render_passports
from pko.render.taxonomy import render_taxonomy
from pko.diff.engine import diff_models

# Технические привязки: пакет реализации и устойчивая идентичность объекта.
TECHNICAL_LINKS = ("package", "limit_key", "stable_key")


class LinkRenderingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo = GitRepo(ensure_fixture())
        versions = select_versions(repo, "master", max_versions=2)
        cls.model = analyze_version(
            repo=repo, version=versions[-1], repo_name="mini_repo", branch="master"
        ).model
        cls.old = analyze_version(
            repo=repo, version=versions[0], repo_name="mini_repo", branch="master"
        ).model

    def test_technical_keys_are_not_rendered_as_links(self):
        """`limit_key` и `stable_key` — не связи; в отчёте они выглядели ссылками."""
        values = {
            v
            for obj in self.model.objects
            for rel, targets in obj.links.items()
            if rel in TECHNICAL_LINKS
            for v in targets
        }
        self.assertTrue(values, "в модели есть технические привязки")

        for name, html in (
            ("taxonomy", render_taxonomy(self.model)),
            ("passports", render_passports(self.model)),
        ):
            for value in values:
                with self.subTest(report=name, value=value):
                    self.assertNotIn(
                        f'class="tag tag-grd">{value}<', html,
                        msg="технический ключ отрисован как ссылка на объект",
                    )

    def test_object_links_are_still_rendered(self):
        html = render_passports(self.model)
        journey = self.model.by_kind("JOURNEY")[0]
        need_id = journey.links["need"][0]
        self.assertIn(need_id, html)
        self.assertTrue(set(journey.links) & REFERENCE_LINKS)

    def test_reports_render_without_errors(self):
        diff = diff_models(self.old, self.model)
        for html in (
            render_taxonomy(self.model, "обзор"),
            render_passports(self.model),
            render_comparison(diff, "оценка"),
        ):
            self.assertTrue(html.startswith("<!DOCTYPE html>"))
            self.assertIn("</html>", html)


if __name__ == "__main__":
    unittest.main()


class ReadabilityTest(unittest.TestCase):
    """Отчёт должен объяснять, а не предъявлять координаты."""

    def _model(self):
        from pko.model.schema import Evidence, PkoModel, PkoObject

        model = PkoModel(meta={"repo": "demo", "commit": "6a225969b0cc", "version_label": "current"})
        obj = PkoObject(id="BBB-001", kind="BBB", name="Приём заявок")
        obj.set(
            "Контракт входа", ["POST /tasks"], "OBSERVED",
            [Evidence(commit="6a225969b0cc", path="api/routes.py", line=42,
                      basis="эндпоинт POST /tasks")],
        )
        model.add(obj)
        return model

    def test_evidence_explains_instead_of_showing_a_hash(self):
        from pko.render.passports import render_passports

        html = render_passports(self._model())
        self.assertIn("эндпоинт POST /tasks", html)
        self.assertIn("api/routes.py:42", html)
        self.assertNotIn("@6a225969", html,
                         msg="хеш коммита один на версию и печатается в шапке")

    def test_object_note_is_shown_above_the_table(self):
        from pko.render.passports import render_passports

        html = render_passports(
            self._model(),
            notes={"BBB-001": "Блок принимает заявки от клиентов и ставит их в работу."},
            overview="Система принимает заявки и обрабатывает их без участия оператора.",
        )
        self.assertIn("Блок принимает заявки", html)
        self.assertIn("О системе", html)
        self.assertLess(html.index("Блок принимает заявки"), html.index("Контракт входа"),
                        msg="пояснение читают до таблицы полей, а не после")

    def test_gate_card_reference_names_what_was_found(self):
        from pko.extractors.base import Fact
        from pko.gate.evaluate import _refs

        facts = [Fact(kind="GRAPH_NODE", key="plan", value="plan",
                      path="agent/graph.py", line=6, basis="узел графа «plan»")]
        self.assertEqual(_refs(facts), ["agent/graph.py:6 — узел графа «plan»"])

    def test_reference_without_explanation_stays_a_location(self):
        from pko.extractors.base import Fact
        from pko.gate.evaluate import _refs

        self.assertEqual(_refs([Fact(kind="LIMIT", key="t", value=1, path="a.py", line=3)]),
                         ["a.py:3"])


class OverviewLoadTest(unittest.TestCase):
    """Обзор отвечает «что здесь есть», а не «чем это доказано»."""

    @classmethod
    def setUpClass(cls):
        repo = GitRepo(ensure_fixture())
        versions = select_versions(repo, "master", max_versions=2)
        cls.analysis = analyze_version(repo=repo, version=versions[-1],
                                       repo_name="mini_repo", branch="master")

    def test_taxonomy_carries_no_evidence_blocks(self):
        """Раньше блоки доказательств занимали в обзоре больше места, чем значения."""
        html = render_taxonomy(self.analysis.model)
        body = html.split("</style>", 1)[1]
        self.assertNotIn('<div class="evidence">', body)
        self.assertNotIn("evidence-where", body)

    def test_badge_marks_only_what_is_not_observed(self):
        """«Найдено в коде» — норма отчёта; помечать норму значит помечать всё."""
        from pko.model.schema import Field
        from pko.render.base import compact_value

        observed = Field(value="POST /tasks", origin="OBSERVED")
        declared = Field(value="Иванова А.А.", origin="DECLARED")
        unknown = Field(value=None, origin="UNKNOWN")

        self.assertNotIn("origin", compact_value(observed))
        self.assertIn("заявлено владельцем", compact_value(declared))
        self.assertIn("не установлено", compact_value(unknown))

    def test_links_column_counts_instead_of_listing_tags(self):
        """У процесса связей полтора десятка: перечень тегов вытеснял всё остальное."""
        html = render_taxonomy(self.analysis.model)
        process = self.analysis.model.by_kind("PROCESS")[0]
        linked = [t for rel, targets in process.links.items()
                  if rel in REFERENCE_LINKS for t in targets]
        self.assertGreater(len(linked), 2, "в фикстуре у процесса есть связи")
        body = html.split("</style>", 1)[1]
        self.assertIn("BBB", body)
        # Полный перечень идентификаторов блоков в обзор не выводится.
        self.assertNotIn(f'tag-bbb">{linked[-1]}<', body)


class CardIndexTest(unittest.TestCase):
    """Паспорта показываются картотекой, а не простынёй."""

    @classmethod
    def setUpClass(cls):
        repo = GitRepo(ensure_fixture())
        versions = select_versions(repo, "master", max_versions=2)
        cls.analysis = analyze_version(repo=repo, version=versions[-1],
                                       repo_name="mini_repo", branch="master")

    def _payload(self, html: str) -> dict:
        import json
        import re

        raw = re.search(r"const passports = (\{.*?\});\n", html, re.S).group(1)
        return json.loads(raw.replace("<\\/", "</"))

    def test_every_object_has_a_card_and_a_passport(self):
        html = render_passports(self.analysis.model)
        objects = self.analysis.model.objects
        self.assertEqual(html.count('class="card"'), len(objects))
        self.assertEqual(set(self._payload(html)), {o.id for o in objects})

    def test_card_description_does_not_repeat_the_title(self):
        """Карточка, дважды повторяющая своё название, читателю не сообщает ничего."""
        import re

        html = render_passports(self.analysis.model)
        pairs = re.findall(
            r'<div class="ctitle"[^>]*>(.*?)</div><span class="cid"[^>]*>.*?</span>'
            r'<div class="cdesc">(.*?)</div>', html)
        self.assertTrue(pairs)
        for title, desc in pairs:
            with self.subTest(title=title):
                self.assertNotEqual(title.strip().lower(), desc.strip().lower())

    def test_note_from_the_writer_wins_over_a_field(self):
        html = render_passports(
            self.analysis.model,
            notes={o.id: "Пояснение писателя" for o in self.analysis.model.objects},
        )
        self.assertIn("Пояснение писателя", html)

    def test_payload_is_escaped_and_does_not_break_the_script(self):
        """Имя с угловой скобкой не должно закрывать тег и ломать страницу."""
        from pko.model.schema import PkoModel, PkoObject

        model = PkoModel(meta={"repo": "d", "commit": "abc123456789", "version_label": "current"})
        obj = PkoObject(id="BBB-001", kind="BBB", name="Блок </script><b>x</b>")
        obj.set("Бизнес-смысл", 'значение с "кавычками"', "OBSERVED", [])
        model.add(obj)

        html = render_passports(model)
        self.assertEqual(html.count("<script>"), html.count("</script>"))
        inside = html.split("const passports = ")[1].split("</script>")[0]
        self.assertNotIn("</script>", inside)
        self.assertIn("Блок", self._payload(html)["BBB-001"]["name"])


class GapsSectionTest(unittest.TestCase):
    """Повторы свёрнуты, статистика прогона — в подвале раздела."""

    def test_repeated_warnings_collapse_into_one_line(self):
        from pko.render.base import split_gaps

        gaps = [
            "Нет готового отчёта о тестах (JUnit XML).",
            "WARN [GRD-001] GUARDRAIL_NOT_TESTED: ограничение не подтверждено тестом",
            "WARN [GRD-002] GUARDRAIL_NOT_TESTED: ограничение не подтверждено тестом",
            "WARN [GRD-003] GUARDRAIL_NOT_TESTED: ограничение не подтверждено тестом",
            "Агент: принято фактов из разведки — 4",
        ]
        shown, run_notes = split_gaps(gaps)

        self.assertEqual(len(shown), 2, msg=shown)
        self.assertIn("GRD-001, GRD-002, GRD-003", shown[1])
        self.assertEqual(run_notes, ["Агент: принято фактов из разведки — 4"])

    def test_run_notes_are_not_counted_as_analysis_gaps(self):
        from pko.model.schema import PkoModel
        from pko.render.base import gaps_section

        model = PkoModel(meta={})
        model.gaps = ["Реальный пробел системы.", "Агент: подключены паки — data"]
        html = gaps_section(model)
        self.assertIn('<span class="count">1</span>', html)
        self.assertIn("О прогоне:", html)


class GuardrailSeverityTest(unittest.TestCase):
    """Severity повторяет последствие проверки Gate, а не назначается на глаз."""

    @classmethod
    def setUpClass(cls):
        repo = GitRepo(ensure_fixture())
        versions = select_versions(repo, "master", max_versions=2)
        cls.model = analyze_version(repo=repo, version=versions[-1],
                                    repo_name="mini_repo", branch="master").model

    def _by_key(self, key: str):
        return next(g for g in self.model.by_kind("GUARDRAIL")
                    if key in (g.links.get("limit_key") or [""])[0])

    def test_read_only_is_a_blocker(self):
        guard = self._by_key("read_only")
        self.assertIn("blocker", guard.get_text("Severity"))
        self.assertIn("CHK-GRD-001", guard.get_text("Severity"))

    def test_numeric_limit_is_an_error(self):
        guard = self._by_key("timeout")
        self.assertIn("error", guard.get_text("Severity"))
        self.assertIn("CHK-GRD-002", guard.get_text("Severity"))

    def test_guardrail_outside_any_check_gets_no_degree(self):
        """Перечень разрешённого ни одна проверка не потребляет — степень не выдумываем."""
        guard = self._by_key("allowlist")
        self.assertEqual(guard.get_text("Severity"), "не участвует в решении о допуске")

    def test_type_and_applies_to_are_present(self):
        guard = self._by_key("read_only")
        self.assertIn("Безопасность", guard.get_text("Тип"))
        self.assertTrue(guard.get_text("Применяется к"))
