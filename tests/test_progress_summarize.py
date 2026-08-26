"""Роль reporter: связный вывод по итогам всех пунктов плана, без сети.

Тот же паттерн, что и у остальных LLM-тестов: `ChatClient._request` скриптуется,
кеш подменяется явным `client`.
"""

import unittest

from pko.errors import LlmError
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.schema import EvidenceRef, ItemVerdict, PlanItem, ProgressModel, UnclaimedGroup
from pko.progress.summarize import summarize_progress

SPEC = ModelSpec(role="reporter", base_url="https://stub.local/v1", model="stub-model", api_key="x")


def _model_with_one_verdict() -> ProgressModel:
    verdict = ItemVerdict(item_id="tasks-api", status="DONE", explanation="Готово.")
    verdict.evidence.append(EvidenceRef(
        path="backend/src/api/v1/router.py", line=7, basis="start_task",
        verified=True, reason="ok",
    ))
    return ProgressModel(
        items={"tasks-api": PlanItem(id="tasks-api", title="API постановки задач")},
        verdicts=[verdict],
        unclaimed=[UnclaimedGroup(group="backend/src", example_paths=["backend/src/config/settings.py"],
                                  file_count=1)],
    )


def scripted(*answers: str):
    queue = list(answers)

    def _request(self, method, path, payload):
        text = queue.pop(0) if queue else "нет данных"
        return {"choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return _request


class SummarizeTest(unittest.TestCase):
    def setUp(self):
        self._original = ChatClient._request
        self.addCleanup(setattr, ChatClient, "_request", self._original)
        self.client = ChatClient(spec=SPEC, use_cache=False)

    def _run(self, response_text: str, model=None):
        ChatClient._request = scripted(response_text)
        return summarize_progress(model or _model_with_one_verdict(), SPEC, client=self.client)

    def test_no_spec_returns_empty_with_note(self):
        result = summarize_progress(_model_with_one_verdict(), spec=None)
        self.assertEqual(result.text, "")
        self.assertIn("не настроена", result.notes[0])

    def test_no_verdicts_returns_empty_with_note(self):
        result = summarize_progress(ProgressModel(), SPEC, client=self.client)
        self.assertEqual(result.text, "")
        self.assertIn("нет вердиктов", result.notes[0])

    def test_valid_summary_is_returned(self):
        result = self._run("Задача постановки задач в обработку реализована и подтверждена кодом.")
        self.assertEqual(result.source, "llm")
        self.assertIn("реализована", result.text)

    def test_summary_naming_an_unknown_path_is_rejected(self):
        result = self._run("На самом деле всё сделано в backend/src/billing_service.py.")
        self.assertEqual(result.text, "")
        self.assertIn("отклонён сторожем", result.notes[0])

    def test_summary_naming_a_verified_path_is_kept(self):
        result = self._run("Подтверждено в backend/src/api/v1/router.py, всё в порядке.")
        self.assertEqual(result.source, "llm")
        self.assertIn("router.py", result.text)

    def test_llm_error_returns_empty_with_note(self):
        def _raise(self, method, path, payload):
            raise LlmError("недоступен")

        ChatClient._request = _raise
        result = summarize_progress(_model_with_one_verdict(), SPEC, client=self.client)
        self.assertEqual(result.text, "")
        self.assertIn("недоступен", result.notes[0])


if __name__ == "__main__":
    unittest.main()
