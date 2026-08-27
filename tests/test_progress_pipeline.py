"""`run_progress` — сквозная сборка ProgressModel, с фокусом на `on_event`.

Остальное поведение пайплайна (сборка ProgressModel/ошибка при неюзабельном
результате) уже покрыто `tests/test_progress_cli.py`/`tests/test_web_app.py`
через CLI и веб-эндпоинт соответственно — здесь только колбэк живого
прогресса, напрямую через `run_progress`, без CLI/HTTP обвязки.
"""

import json
import tempfile
import unittest
from pathlib import Path

import pko.llm.client as client_module
from fixture_support import ensure_fixture
from pko.extractors.base import Tree
from pko.git.repo import GitRepo
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.pipeline import run_progress
from pko.progress.target_repo import TargetRepo, load_target
from test_progress_pptx import build_sample_deck

SPEC = ModelSpec(role="matcher", base_url="https://stub.local/v1", model="stub-model", api_key="x")


def _tool_call(call_id: str, name: str, **args) -> dict:
    return {"content": None, "tool_calls": [{
        "id": call_id, "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }]}


def scripted(*answers):
    queue = list(answers)

    def _request(self, method, path, payload):
        item = queue.pop(0) if queue else json.dumps({})
        message = {"content": item} if isinstance(item, str) else item
        return {"choices": [{"message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return _request


class RunProgressOnEventTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp_path = Path(self.tmp.name)

        self.plan_path = tmp_path / "plan.pptx"
        build_sample_deck(self.plan_path)

        repo = GitRepo(ensure_fixture())
        self.target: TargetRepo = load_target(repo, "master")

        self._original_cache_dir = client_module.DEFAULT_CACHE_DIR
        client_module.DEFAULT_CACHE_DIR = tmp_path / "llm-cache"
        self.addCleanup(setattr, client_module, "DEFAULT_CACHE_DIR", self._original_cache_dir)

        self._original_request = ChatClient._request
        self.addCleanup(setattr, ChatClient, "_request", self._original_request)

    def test_on_event_receives_phases_and_claims_in_order(self):
        submit_answer = _tool_call(
            "call_submit", "submit_verdict",
            item_id="tasks-api", title="API постановки задач", source_slide=2,
            status="DONE", explanation="Эндпоинт постановки задачи реализован.",
            evidence=[{"path": "backend/src/api/v1/router.py", "line": 7,
                      "basis": "функция start_task ставит задачу в обработку"}],
        )
        finish_answer = _tool_call("call_finish", "finish")
        ChatClient._request = scripted(submit_answer, finish_answer)

        events: list[tuple[str, dict]] = []
        model = run_progress(
            self.plan_path, "demo-repo", self.target, SPEC,
            on_event=lambda kind, data: events.append((kind, data)),
        )

        self.assertEqual([kind for kind, _ in events], [
            "presentation_parsed", "claim_verified", "summarizing",
        ])
        self.assertGreater(events[0][1]["slide_count"], 0)
        self.assertEqual(events[1][1], {"title": "API постановки задач", "status": "DONE"})
        self.assertEqual(events[2][1], {})
        # sanity: колбэк — побочный эффект, сама модель собирается как обычно.
        self.assertEqual(model.counts()["DONE"], 1)

    def test_without_on_event_behaves_exactly_as_before(self):
        submit_answer = _tool_call(
            "call_submit", "submit_verdict",
            item_id="tasks-api", title="API постановки задач", source_slide=2,
            status="NOT_STARTED", explanation="Кода не нашлось.",
        )
        finish_answer = _tool_call("call_finish", "finish")
        ChatClient._request = scripted(submit_answer, finish_answer)

        model = run_progress(self.plan_path, "demo-repo", self.target, SPEC)
        self.assertEqual(model.counts()["NOT_STARTED"], 1)


if __name__ == "__main__":
    unittest.main()
