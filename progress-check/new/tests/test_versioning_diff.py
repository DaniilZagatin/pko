"""Детерминированный diff между двумя снимками: change_type, readiness_delta,

ADDED/REMOVED. Чистые функции — без LLM и без БД.
"""

import unittest

from pko.progress.schema import ItemVerdict, PlanItem, ProgressModel
from pko.versioning.diff import ADDED, IMPROVED, REGRESSED, REMOVED, UNCHANGED, compute_comparison


def _model(verdicts: list[ItemVerdict]) -> ProgressModel:
    items = {v.item_id: PlanItem(id=v.item_id, title=f"Этап {v.item_id}", source_slide=1)
             for v in verdicts}
    return ProgressModel(items=items, verdicts=verdicts)


def _verdict(item_id: str, status: str, canonical_stage_id: str, progress: int = 0) -> ItemVerdict:
    return ItemVerdict(item_id=item_id, status=status, explanation="", progress=progress,
                        canonical_stage_id=canonical_stage_id)


class ComputeComparisonTest(unittest.TestCase):
    def test_not_started_to_partial_is_improved(self):
        from_model = _model([_verdict("a", "NOT_STARTED", "cs1")])
        to_model = _model([_verdict("a", "PARTIAL", "cs1", progress=40)])
        comparison = compute_comparison(from_model, to_model)
        [delta] = comparison.stage_deltas
        self.assertEqual(delta.change_type, IMPROVED)
        self.assertEqual(delta.previous_readiness, 0)
        self.assertEqual(delta.current_readiness, 40)
        self.assertEqual(delta.readiness_delta, 40)

    def test_partial_to_done_is_improved(self):
        from_model = _model([_verdict("a", "PARTIAL", "cs1", progress=60)])
        to_model = _model([_verdict("a", "DONE", "cs1")])
        [delta] = compute_comparison(from_model, to_model).stage_deltas
        self.assertEqual(delta.change_type, IMPROVED)

    def test_done_to_partial_is_regressed(self):
        from_model = _model([_verdict("a", "DONE", "cs1")])
        to_model = _model([_verdict("a", "PARTIAL", "cs1", progress=50)])
        [delta] = compute_comparison(from_model, to_model).stage_deltas
        self.assertEqual(delta.change_type, REGRESSED)

    def test_same_status_is_unchanged_even_with_different_titles(self):
        from_model = _model([_verdict("a", "PARTIAL", "cs1", progress=30)])
        to_model = _model([_verdict("b", "PARTIAL", "cs1", progress=30)])
        [delta] = compute_comparison(from_model, to_model).stage_deltas
        self.assertEqual(delta.change_type, UNCHANGED)

    def test_partial_progress_within_same_status_still_reports_readiness_delta(self):
        # План версионирования §9: UNCHANGED по статусу, но readiness внутри
        # статуса всё равно должен быть виден как прогресс.
        from_model = _model([_verdict("a", "PARTIAL", "cs1", progress=35)])
        to_model = _model([_verdict("a", "PARTIAL", "cs1", progress=70)])
        [delta] = compute_comparison(from_model, to_model).stage_deltas
        self.assertEqual(delta.change_type, UNCHANGED)
        self.assertEqual(delta.readiness_delta, 35)

    def test_stage_only_in_to_is_added(self):
        from_model = _model([])
        to_model = _model([_verdict("a", "PARTIAL", "cs1", progress=20)])
        [delta] = compute_comparison(from_model, to_model).stage_deltas
        self.assertEqual(delta.change_type, ADDED)
        self.assertIsNone(delta.previous_status)
        self.assertIsNone(delta.previous_readiness)
        self.assertEqual(delta.current_status, "PARTIAL")

    def test_stage_only_in_from_is_removed(self):
        from_model = _model([_verdict("a", "DONE", "cs1")])
        to_model = _model([])
        [delta] = compute_comparison(from_model, to_model).stage_deltas
        self.assertEqual(delta.change_type, REMOVED)
        self.assertIsNone(delta.current_status)
        self.assertIsNone(delta.current_readiness)

    def test_verdicts_without_canonical_stage_id_are_excluded(self):
        from_model = _model([_verdict("a", "DONE", "")])
        to_model = _model([_verdict("a", "DONE", "")])
        comparison = compute_comparison(from_model, to_model)
        self.assertEqual(comparison.stage_deltas, [])

    def test_readiness_before_after_delta_use_done_ratio_not_percent_average(self):
        from_model = _model([_verdict("a", "NOT_STARTED", "cs1"), _verdict("b", "DONE", "cs2")])
        to_model = _model([_verdict("a", "DONE", "cs1"), _verdict("b", "DONE", "cs2")])
        comparison = compute_comparison(from_model, to_model)
        self.assertEqual(comparison.readiness_before, 0.5)
        self.assertEqual(comparison.readiness_after, 1.0)
        self.assertEqual(comparison.readiness_delta, 0.5)

    def test_stage_order_is_from_first_then_new_added_stages(self):
        from_model = _model([_verdict("a", "DONE", "cs1"), _verdict("b", "DONE", "cs2")])
        to_model = _model([
            _verdict("a", "DONE", "cs1"), _verdict("b", "DONE", "cs2"),
            _verdict("c", "PARTIAL", "cs3", progress=10),
        ])
        comparison = compute_comparison(from_model, to_model)
        self.assertEqual([d.canonical_stage_id for d in comparison.stage_deltas], ["cs1", "cs2", "cs3"])


if __name__ == "__main__":
    unittest.main()
