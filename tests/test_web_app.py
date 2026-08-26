"""Веб-эндпоинт `pko progress` — тот же контракт, что и CLI, плюс HTTP-обвязка.

`TestClient` — синхронный, без реального порта. LLM подменяется тем же
способом, что и в `test_progress_cli.py`: скриптованный `ChatClient._request`
+ `DEFAULT_CACHE_DIR` во временном каталоге, иначе прогон писал бы в реальный
`~/.pko/llm-cache`.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import pko.llm.client as client_module
from fixture_support import ensure_fixture
from pko.llm.client import ChatClient
from pko.web.app import app
from test_progress_pptx import build_sample_deck

ENV_KEYS = ("PKO_ASSEMBLER_BASE_URL", "PKO_ASSEMBLER_MODEL", "PKO_ASSEMBLER_API_KEY")


def scripted(*answers: str):
    queue = list(answers)

    def _request(self, method, path, payload):
        text = queue.pop(0) if queue else json.dumps({})
        return {"choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return _request


class WebAppTest(unittest.TestCase):
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

    def _post(self, repo: str, branch: str = "", plan_path: Path | None = None):
        plan_path = plan_path or self.plan_path
        with open(plan_path, "rb") as fh:
            return self.client.post(
                "/api/progress",
                files={"plan": (plan_path.name, fh, "application/octet-stream")},
                data={"repo": repo, "branch": branch},
            )

    def test_index_serves_the_form(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("progress-form", resp.text)
        self.assertIn('name="plan"', resp.text)

    def test_full_run_returns_html_result(self):
        plan_answer = json.dumps({"items": [
            {"id": "tasks-api", "title": "API постановки задач", "source_slide": 2},
        ]})
        # Агентный matcher: один пункт плана — один изолированный цикл, модель
        # сразу отвечает финальным текстом (без tool_calls).
        match_answer = json.dumps({
            "status": "DONE",
            "explanation": "Эндпоинт постановки задачи реализован.",
            "evidence": [{"path": "backend/src/api/v1/router.py", "line": 7,
                         "basis": "функция start_task ставит задачу в обработку"}],
        })
        summary_answer = "Задача постановки задач реализована и подтверждена кодом."
        ChatClient._request = scripted(plan_answer, match_answer, summary_answer)

        resp = self._post(repo=str(ensure_fixture()))
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data["counts"]["DONE"], 1)
        self.assertEqual(data["progress_ratio"], 1.0)
        self.assertIn("API постановки задач", data["html"])
        self.assertIn("реализована и подтверждена", data["html"])
        self.assertIn("backend/src/api/v1/router.py:7", data["html"])

    def test_missing_plan_file_is_a_validation_error(self):
        resp = self.client.post(
            "/api/progress", data={"repo": str(ensure_fixture()), "branch": ""}
        )
        self.assertEqual(resp.status_code, 422)

    def test_non_pptx_filename_is_rejected(self):
        bad = Path(self.tmp.name) / "plan.txt"
        bad.write_text("не презентация", encoding="utf-8")
        resp = self._post(repo=str(ensure_fixture()), plan_path=bad)
        self.assertEqual(resp.status_code, 400)
        self.assertIn(".pptx", resp.json()["message"])

    def test_nonexistent_local_repo_is_a_clean_400_not_a_500(self):
        ChatClient._request = scripted(json.dumps({"items": []}))
        missing = str(Path(self.tmp.name) / "no-such-repo")
        resp = self._post(repo=missing)
        self.assertEqual(resp.status_code, 400)
        self.assertTrue(resp.json()["message"])

    def test_missing_llm_config_is_a_clean_400(self):
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        resp = self._post(repo=str(ensure_fixture()))
        self.assertEqual(resp.status_code, 400)
        self.assertIn("LLM", resp.json()["message"])


if __name__ == "__main__":
    unittest.main()
