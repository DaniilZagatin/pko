"""Дашборд-путь (`_journey_section`) в HTML-отчёте: контракт данных для
React-бандла (`frontend-app/`) — без сети, без LLM, без реальной сборки
бандла (она подменяется в большинстве тестов; геометрия шеврона и перенос
текста тестируются отдельно, в `frontend-app/src/chevron-geometry.test.ts`).
"""

import json
import unittest
from unittest import mock

from pko.errors import PkoError
from pko.progress.schema import EvidenceRef, ItemVerdict, PlanItem, ProgressModel
from pko.render.progress_report import display_percent, render_progress_report

_FAKE_BUNDLE = ("<style>.fake{}</style>", '<script type="module">/*fake bundle*/</script>')


def _model(verdicts: list[ItemVerdict]) -> ProgressModel:
    items = {v.item_id: PlanItem(id=v.item_id, title=f"Пункт {v.item_id}", source_slide=1)
             for v in verdicts}
    return ProgressModel(items=items, verdicts=verdicts)


class DisplayPercentTest(unittest.TestCase):
    def test_done_is_always_100(self):
        v = ItemVerdict(item_id="a", status="DONE", explanation="x", progress=10)
        self.assertEqual(display_percent(v), 100)

    def test_not_started_is_always_0(self):
        v = ItemVerdict(item_id="a", status="NOT_STARTED", explanation="x", progress=90)
        self.assertEqual(display_percent(v), 0)

    def test_partial_uses_agents_own_progress(self):
        v = ItemVerdict(item_id="a", status="PARTIAL", explanation="x", progress=30)
        self.assertEqual(display_percent(v), 30)

    def test_partial_without_progress_defaults_to_50(self):
        v = ItemVerdict(item_id="a", status="PARTIAL", explanation="x")
        self.assertEqual(display_percent(v), 50)

    def test_unclear_without_progress_defaults_to_50(self):
        v = ItemVerdict(item_id="a", status="UNCLEAR", explanation="x")
        self.assertEqual(display_percent(v), 50)


class JourneySectionTest(unittest.TestCase):
    """Реальная сборка бандла не нужна — `_load_journey_bundle` подменяется:
    эти тесты проверяют, что Python передаёт React верные данные, а не то,
    как React их рисует."""

    def setUp(self):
        patcher = mock.patch(
            "pko.render.progress_report._load_journey_bundle", return_value=_FAKE_BUNDLE
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_empty_verdicts_produce_no_section(self):
        html = render_progress_report(ProgressModel())
        self.assertNotIn('<div id="journey-root">', html)

    def test_mount_point_and_bundle_are_embedded(self):
        verdicts = [ItemVerdict(item_id="a", status="DONE", explanation="Готово.")]
        html = render_progress_report(_model(verdicts))
        self.assertIn('<div id="journey-root">', html)
        self.assertIn(_FAKE_BUNDLE[0], html)
        self.assertIn(_FAKE_BUNDLE[1], html)

    def test_items_json_has_correct_fields_per_verdict(self):
        verdicts = [
            ItemVerdict(item_id="a", status="DONE", explanation="Готово."),
            ItemVerdict(item_id="b", status="NOT_STARTED", explanation="Не начато."),
            ItemVerdict(item_id="c", status="PARTIAL", explanation="Частично.", progress=40),
        ]
        html = render_progress_report(_model(verdicts))
        start = html.index("window.__JOURNEY_ITEMS__ = ") + len("window.__JOURNEY_ITEMS__ = ")
        end = html.index(";</script>", start)
        items = json.loads(html[start:end])
        self.assertEqual(len(items), 3)
        by_status = {i["status"]: i for i in items}
        self.assertEqual(by_status["DONE"]["pct"], 100)
        self.assertEqual(by_status["DONE"]["color"], "green")
        self.assertEqual(by_status["NOT_STARTED"]["pct"], 0)
        self.assertEqual(by_status["NOT_STARTED"]["color"], "red")
        self.assertEqual(by_status["PARTIAL"]["pct"], 40)
        self.assertEqual(by_status["PARTIAL"]["explanation"], "Частично.")
        self.assertEqual(by_status["PARTIAL"]["title"], "Пункт c")
        self.assertEqual(by_status["PARTIAL"]["label"], "Частично")

    def test_renders_fine_for_a_verdict_carrying_evidence(self):
        # Раздел с постатусными карточками (и отдельным показом evidence)
        # убран по просьбе пользователя — вся эта информация теперь только
        # в комментарии внутри дашборда «Путь». Тест на то, что сама секция
        # не падает и не блокируется, если у вердикта есть evidence.
        verdict = ItemVerdict(item_id="a", status="DONE", explanation="x")
        verdict.evidence.append(EvidenceRef(path="x.py", line=1, basis="x", verified=True, reason="ok"))
        html = render_progress_report(_model([verdict]))
        self.assertIn('<div id="journey-root">', html)


class JourneyBundleMissingTest(unittest.TestCase):
    def test_missing_bundle_raises_a_clean_pko_error(self):
        verdicts = [ItemVerdict(item_id="a", status="DONE", explanation="x")]
        with mock.patch("pko.render.progress_report._JOURNEY_BUNDLE_PATH") as fake_path:
            fake_path.exists.return_value = False
            with self.assertRaises(PkoError) as ctx:
                render_progress_report(_model(verdicts))
            self.assertIn("не собран", ctx.exception.message)
            self.assertIn("npm run build", ctx.exception.hint)


if __name__ == "__main__":
    unittest.main()
