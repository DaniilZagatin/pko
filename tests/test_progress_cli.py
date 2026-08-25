"""`pko progress` из командной строки — сквозной прогон с заглушенным LLM.

Транспорт подменяется тем же способом, что и в остальных LLM-тестах. Кеш
дополнительно уводится во временный каталог: `cmd_progress` строит `ChatClient`
внутри `extract_plan`/`match_plan` без инъекции тестового клиента (в отличие от
`pko.agent.loop.run_scout`), поэтому без этого прогон писал бы в реальный
`~/.pko/llm-cache` — ровно та утечка, которую нашли в тестах самого PKO.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

import pko.llm.client as client_module
from fixture_support import ensure_fixture
from pko import cli
from pko.llm.client import ChatClient
from test_progress_pptx import build_sample_deck

ENV_KEYS = ("PKO_ASSEMBLER_BASE_URL", "PKO_ASSEMBLER_MODEL", "PKO_ASSEMBLER_API_KEY")


def scripted(*answers: str):
    queue = list(answers)

    def _request(self, method, path, payload):
        text = queue.pop(0) if queue else json.dumps({})
        return {"choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return _request


class ProgressCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp_path = Path(self.tmp.name)

        self.plan_path = tmp_path / "plan.pptx"
        build_sample_deck(self.plan_path)
        self.out_dir = tmp_path / "out"

        # Кеш ChatClient — во временный каталог, а не в реальный ~/.pko/llm-cache.
        self._original_cache_dir = client_module.DEFAULT_CACHE_DIR
        client_module.DEFAULT_CACHE_DIR = tmp_path / "llm-cache"
        self.addCleanup(setattr, client_module, "DEFAULT_CACHE_DIR", self._original_cache_dir)

        self._original_request = ChatClient._request
        self.addCleanup(setattr, ChatClient, "_request", self._original_request)

        self._original_env = {k: os.environ.get(k) for k in ENV_KEYS}
        os.environ.update({
            "PKO_ASSEMBLER_BASE_URL": "https://stub.local/v1",
            "PKO_ASSEMBLER_MODEL": "stub-model",
            "PKO_ASSEMBLER_API_KEY": "x",
        })

        def _restore_env():
            for k, v in self._original_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

        self.addCleanup(_restore_env)

    def test_full_run_produces_report_and_model(self):
        plan_answer = json.dumps({"items": [
            {"id": "tasks-api", "title": "API постановки задач", "source_slide": 2},
        ]})
        match_answer = json.dumps({"verdicts": [
            {"item_id": "tasks-api", "status": "DONE",
             "explanation": "Эндпоинт постановки задачи реализован.",
             "evidence": [{"path": "backend/src/api/v1/router.py", "line": 7,
                           "basis": "функция start_task ставит задачу в обработку"}]},
        ]})
        ChatClient._request = scripted(plan_answer, match_answer)

        exit_code = cli.main([
            "progress", str(self.plan_path),
            "--repo-path", str(ensure_fixture()),
            "--out", str(self.out_dir),
        ])

        self.assertEqual(exit_code, 0)
        report = self.out_dir / "progress_report.html"
        model_json = self.out_dir / "progress_model.json"
        self.assertTrue(report.exists())
        self.assertTrue(model_json.exists())

        model = json.loads(model_json.read_text(encoding="utf-8"))
        self.assertEqual(model["counts"]["DONE"], 1)
        self.assertEqual(model["progress_ratio"], 1.0)

        html = report.read_text(encoding="utf-8")
        self.assertIn("API постановки задач", html)
        self.assertIn("backend/src/api/v1/router.py:7", html)

        self.assertFalse(self._original_cache_dir.exists() and
                         any(self._original_cache_dir.iterdir()),
                         msg="прогон не должен трогать реальный ~/.pko/llm-cache")

    def test_missing_plan_file_fails_before_touching_repo(self):
        exit_code = cli.main([
            "progress", str(Path(self.tmp.name) / "does-not-exist.pptx"),
            "--repo-path", str(ensure_fixture()),
            "--out", str(self.out_dir),
        ])
        self.assertEqual(exit_code, 1)
        self.assertFalse(self.out_dir.exists())


if __name__ == "__main__":
    unittest.main()
