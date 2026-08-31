"""Продукты: создание, поиск, список со сводкой по последнему снимку."""

import tempfile
import unittest
from pathlib import Path

from pko.errors import PkoError
from pko.progress.schema import ItemVerdict, PlanItem, ProgressModel
from pko.store import products, snapshots


def _model(readiness_status: str = "DONE") -> ProgressModel:
    item = PlanItem(id="s1", title="Этап 1", source_slide=1)
    verdict = ItemVerdict(item_id="s1", status=readiness_status, explanation="", progress=100)
    return ProgressModel(items={"s1": item}, verdicts=[verdict])


class ProductsStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "store.db"

    def test_create_and_get_product_roundtrip(self):
        product = products.create_product("Клиентский путь", db_path=self.db_path)
        self.assertTrue(product.id.startswith("prod_"))
        self.assertEqual(product.name, "Клиентский путь")

        fetched = products.get_product(product.id, db_path=self.db_path)
        self.assertEqual(fetched, product)

    def test_get_unknown_product_returns_none_not_error(self):
        self.assertIsNone(products.get_product("prod_doesnotexist", db_path=self.db_path))

    def test_empty_name_is_rejected(self):
        with self.assertRaises(PkoError):
            products.create_product("   ", db_path=self.db_path)

    def test_list_products_newest_first_with_snapshot_summary(self):
        older = products.create_product("Продукт A", db_path=self.db_path)
        newer = products.create_product("Продукт B", db_path=self.db_path)
        snapshots.save_snapshot(newer.id, _model(), {}, db_path=self.db_path)

        listed = products.list_products(db_path=self.db_path)
        self.assertEqual([p.product.id for p in listed], [newer.id, older.id])

        newer_summary = listed[0]
        self.assertEqual(newer_summary.snapshot_count, 1)
        self.assertEqual(newer_summary.latest_readiness, 1.0)
        self.assertIsNotNone(newer_summary.latest_created_at)

        older_summary = listed[1]
        self.assertEqual(older_summary.snapshot_count, 0)
        self.assertIsNone(older_summary.latest_readiness)


if __name__ == "__main__":
    unittest.main()
