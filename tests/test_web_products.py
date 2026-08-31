"""`/api/products*` — создание продукта и список без завязки на реальный анализ

(сам путь «анализ -> snapshot продукта» покрыт в
`test_web_analyses.py::test_analysis_with_product_id_is_persisted_as_snapshot`).
"""

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from pko.web.app import app


class WebProductsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._original_data_dir = os.environ.get("PKO_DATA_DIR")
        os.environ["PKO_DATA_DIR"] = str(Path(self.tmp.name) / "pko-data")

        def _restore():
            if self._original_data_dir is None:
                os.environ.pop("PKO_DATA_DIR", None)
            else:
                os.environ["PKO_DATA_DIR"] = self._original_data_dir

        self.addCleanup(_restore)

    def test_create_product_returns_id_and_name(self):
        resp = self.client.post("/api/products", data={"name": "Клиентский путь"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["id"].startswith("prod_"))
        self.assertEqual(body["name"], "Клиентский путь")

    def test_create_product_with_empty_name_is_rejected(self):
        resp = self.client.post("/api/products", data={"name": "   "})
        self.assertEqual(resp.status_code, 400)

    def test_list_products_includes_created_ones_newest_first(self):
        first = self.client.post("/api/products", data={"name": "A"}).json()
        second = self.client.post("/api/products", data={"name": "B"}).json()

        listed = self.client.get("/api/products").json()
        self.assertEqual([p["id"] for p in listed], [second["id"], first["id"]])
        self.assertEqual(listed[0]["snapshot_count"], 0)
        self.assertIsNone(listed[0]["latest_readiness"])

    def test_get_product_by_id(self):
        created = self.client.post("/api/products", data={"name": "Клиентский путь"}).json()
        resp = self.client.get(f"/api/products/{created['id']}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), created)

    def test_get_unknown_product_by_id_is_404(self):
        resp = self.client.get("/api/products/prod_doesnotexist")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Продукт не найден", resp.json()["message"])

    def test_snapshots_of_unknown_product_is_404(self):
        resp = self.client.get("/api/products/prod_doesnotexist/snapshots")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Продукт не найден", resp.json()["message"])

    def test_unknown_snapshot_of_known_product_is_404(self):
        product_id = self.client.post("/api/products", data={"name": "A"}).json()["id"]
        resp = self.client.get(f"/api/products/{product_id}/snapshots/snap_doesnotexist")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Проверка не найдена", resp.json()["message"])


if __name__ == "__main__":
    unittest.main()
