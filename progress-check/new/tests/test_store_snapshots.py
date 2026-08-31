"""Снимки: версии продукта, конкурентное сохранение, roundtrip модели через JSON."""

import tempfile
import threading
import unittest
from pathlib import Path

from pko.progress.schema import EvidenceRef, ItemVerdict, PlanItem, ProgressModel
from pko.store import products, snapshots


def _model(status: str = "PARTIAL", progress: int = 40) -> ProgressModel:
    item = PlanItem(id="s1", title="Сборка предложения", stage="Спринт 1",
                     description="Собрать КП", source_slide=2)
    verdict = ItemVerdict(
        item_id="s1", status=status, explanation="Основной сценарий реализован.",
        progress=progress,
        evidence=[EvidenceRef(path="a/b.py", line=10, basis="функция build_offer",
                               verified=True, reason="")],
    )
    return ProgressModel(
        meta={"repo": "demo", "branch": "main", "commit": "abc123"},
        items={"s1": item}, verdicts=[verdict], gaps=["пример пробела"],
        summary="Итог.", summary_source="llm",
    )


class SnapshotsStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "store.db"
        self.product = products.create_product("Продукт", db_path=self.db_path)

    def test_first_snapshot_is_version_one(self):
        snap = snapshots.save_snapshot(self.product.id, _model(), {"repo": {"commit": "a"}},
                                        db_path=self.db_path)
        self.assertEqual(snap.version_number, 1)
        self.assertEqual(snap.overall_readiness, 0.0)  # PARTIAL не засчитывается как DONE

    def test_version_numbers_increment_per_product(self):
        snapshots.save_snapshot(self.product.id, _model(), {}, db_path=self.db_path)
        second = snapshots.save_snapshot(self.product.id, _model(), {}, db_path=self.db_path)
        self.assertEqual(second.version_number, 2)

        other_product = products.create_product("Другой продукт", db_path=self.db_path)
        first_for_other = snapshots.save_snapshot(other_product.id, _model(), {},
                                                    db_path=self.db_path)
        self.assertEqual(first_for_other.version_number, 1)

    def test_model_roundtrips_through_json_storage(self):
        original = _model()
        saved = snapshots.save_snapshot(self.product.id, original, {"files": []},
                                         db_path=self.db_path)
        loaded = snapshots.get_snapshot(saved.id, db_path=self.db_path)

        self.assertEqual(loaded.model.meta, original.meta)
        self.assertEqual(loaded.model.gaps, original.gaps)
        self.assertEqual(loaded.model.summary, original.summary)
        [loaded_verdict] = loaded.model.verdicts
        [original_verdict] = original.verdicts
        self.assertEqual(loaded_verdict.status, original_verdict.status)
        self.assertEqual(loaded_verdict.progress, original_verdict.progress)
        [loaded_evidence] = loaded_verdict.evidence
        self.assertEqual(loaded_evidence.path, "a/b.py")
        self.assertTrue(loaded_evidence.verified)
        self.assertEqual(loaded.model.items["s1"].title, "Сборка предложения")

    def test_list_snapshots_oldest_first(self):
        first = snapshots.save_snapshot(self.product.id, _model(), {}, db_path=self.db_path)
        second = snapshots.save_snapshot(self.product.id, _model(), {}, db_path=self.db_path)
        listed = snapshots.list_snapshots(self.product.id, db_path=self.db_path)
        self.assertEqual([s.id for s in listed], [first.id, second.id])

    def test_get_unknown_snapshot_returns_none(self):
        self.assertIsNone(snapshots.get_snapshot("snap_doesnotexist", db_path=self.db_path))

    def test_concurrent_saves_for_same_product_do_not_collide_on_version_number(self):
        results: list[int] = []
        lock = threading.Lock()

        def _save():
            snap = snapshots.save_snapshot(self.product.id, _model(), {}, db_path=self.db_path)
            with lock:
                results.append(snap.version_number)

        threads = [threading.Thread(target=_save) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sorted(results), list(range(1, 9)))


if __name__ == "__main__":
    unittest.main()
