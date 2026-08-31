"""`GET /api/products/{id}/compare` — от создания продукта до готовой дельты

между двумя реальными прогонами анализа. Матчинг этапов здесь через точное
совпадение названия (не тестирует сам матчер — это `test_versioning_canonical.py`),
фокус — что весь путь product -> snapshot -> compare действительно склеен.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import pko.llm.client as client_module
from pko.llm.client import ChatClient
from pko.store import snapshots as snapshots_store
from pko.web.app import app
from test_progress_pptx import build_sample_deck

ENV_KEYS = ("PKO_ASSEMBLER_BASE_URL", "PKO_ASSEMBLER_MODEL", "PKO_ASSEMBLER_API_KEY")


def _tool_call(call_id: str, name: str, **kwargs) -> dict:
    return {"content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(kwargs, ensure_ascii=False)},
    }]}


def _scripted(*answers):
    queue = list(answers)

    def _request(self, method, path, payload):
        item = queue.pop(0) if queue else json.dumps({})
        message = {"content": item} if isinstance(item, str) else item
        return {"choices": [{"message": message}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    return _request


class ProductsCompareTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.plan_path = Path(self.tmp.name) / "plan.pptx"
        build_sample_deck(self.plan_path)

        self._original_cache_dir = client_module.DEFAULT_CACHE_DIR
        client_module.DEFAULT_CACHE_DIR = Path(self.tmp.name) / "llm-cache"
        self.addCleanup(setattr, client_module, "DEFAULT_CACHE_DIR", self._original_cache_dir)

        self._original_request = ChatClient._request
        self.addCleanup(setattr, ChatClient, "_request", self._original_request)

        self._original_env = {k: os.environ.get(k) for k in ENV_KEYS + ("PKO_DATA_DIR",)}
        os.environ.update({
            "PKO_ASSEMBLER_BASE_URL": "https://stub.local/v1",
            "PKO_ASSEMBLER_MODEL": "stub-model",
            "PKO_ASSEMBLER_API_KEY": "x",
            "PKO_DATA_DIR": str(Path(self.tmp.name) / "pko-data"),
        })

        def _restore_env():
            for k, v in self._original_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.addCleanup(_restore_env)

        self.product_id = self.client.post("/api/products", data={"name": "Продукт"}).json()["id"]

    def _run_analysis(self, status: str) -> dict:
        # Одна и та же презентация в обоих прогонах даёт одинаковый payload
        # первого хода агента (`ChatClient.chat` кеширует по всему payload,
        # без учёта скриптованной очереди ответов) — отдельный подкаталог
        # кеша на каждый статус, иначе второй прогон получил бы из кеша
        # вердикт первого вместо своего собственного.
        client_module.DEFAULT_CACHE_DIR = Path(self.tmp.name) / "llm-cache" / status
        submit = _tool_call(
            "call_submit", "submit_verdict",
            item_id="tasks-api", title="API постановки задач", source_slide=2,
            status=status, explanation="Комментарий.",
        )
        finish = _tool_call("call_finish", "finish")
        summary = "Сводный вывод."
        ChatClient._request = _scripted(submit, finish, summary)

        with open(self.plan_path, "rb") as fh:
            create_resp = self.client.post(
                "/api/analyses",
                files=[("presentation", (self.plan_path.name, fh, "application/octet-stream"))],
                data={"repository": "", "branch": "", "product_id": self.product_id},
            )
        self.assertEqual(create_resp.status_code, 200, create_resp.text)
        analysis_id = create_resp.json()["analysis_id"]
        self.client.get(f"/api/analyses/{analysis_id}/events")  # дождаться завершения
        return self.client.get(f"/api/analyses/{analysis_id}").json()

    def test_compare_reflects_status_change_between_two_runs(self):
        first = self._run_analysis("NOT_STARTED")
        second = self._run_analysis("DONE")
        self.assertEqual(first["version_number"], 1)
        self.assertEqual(second["version_number"], 2)

        resp = self.client.get(
            f"/api/products/{self.product_id}/compare",
            params={"from": first["snapshot_id"], "to": second["snapshot_id"]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["readiness_before"], 0.0)
        self.assertEqual(body["readiness_after"], 1.0)
        self.assertEqual(body["readiness_delta"], 1.0)
        [delta] = body["stage_deltas"]
        self.assertEqual(delta["change_type"], "IMPROVED")
        self.assertEqual(delta["previous_status"], "NOT_STARTED")
        self.assertEqual(delta["current_status"], "DONE")

    def test_compare_is_cached_on_second_request(self):
        first = self._run_analysis("NOT_STARTED")
        second = self._run_analysis("DONE")
        params = {"from": first["snapshot_id"], "to": second["snapshot_id"]}

        first_response = self.client.get(f"/api/products/{self.product_id}/compare", params=params).json()
        second_response = self.client.get(f"/api/products/{self.product_id}/compare", params=params).json()
        self.assertEqual(first_response, second_response)

    def test_compare_includes_llm_business_interpretation_when_reporter_answers(self):
        first = self._run_analysis("NOT_STARTED")
        second = self._run_analysis("DONE")

        # canonical_stage_id читаем напрямую из сохранённого snapshot'а — не
        # через первый вызов /compare: тот вызвал бы interpret_comparison с
        # тем же (исчерпанным) скриптом ChatClient, что и второй прогон
        # анализа, закэшировал бы пустой, но формально успешный ("llm")
        # ответ, и настоящий сценарий этого теста (первый реальный ответ
        # reporter'а на сравнение) стало бы невозможно проверить.
        [verdict] = snapshots_store.get_snapshot(second["snapshot_id"]).model.verdicts
        stage_id = verdict.canonical_stage_id
        self.assertTrue(stage_id)

        client_module.DEFAULT_CACHE_DIR = Path(self.tmp.name) / "llm-cache" / "compare"
        interpretation = json.dumps({
            "progress_summary": "Этап доведён до полной готовности за период.",
            "stage_business_deltas": {stage_id: "Реализация завершена и подтверждена кодом."},
            "risks": [{"text": "Нагрузочное тестирование не проводилось.", "state": "NEW"}],
            "next_focus": ["Провести нагрузочное тестирование."],
        })
        ChatClient._request = _scripted(interpretation)
        resp = self.client.get(
            f"/api/products/{self.product_id}/compare",
            params={"from": first["snapshot_id"], "to": second["snapshot_id"]},
        )
        body = resp.json()
        self.assertEqual(body["progress_summary"], "Этап доведён до полной готовности за период.")
        self.assertEqual(body["current_risks"],
                          [{"text": "Нагрузочное тестирование не проводилось.", "state": "NEW"}])
        self.assertEqual(body["next_focus"], ["Провести нагрузочное тестирование."])
        [delta] = body["stage_deltas"]
        self.assertEqual(delta["business_delta"], "Реализация завершена и подтверждена кодом.")

    def test_comparing_backwards_is_rejected(self):
        first = self._run_analysis("NOT_STARTED")
        second = self._run_analysis("DONE")
        resp = self.client.get(
            f"/api/products/{self.product_id}/compare",
            params={"from": second["snapshot_id"], "to": first["snapshot_id"]},
        )
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
