"""Бизнес-интерпретация сравнения (`versioning/interpret.py`) — необязательная

роль `reporter`, тот же принцип деградации, что и у `progress/summarize.py`:
без роли/при сбое — пустой результат с причиной в notes, не шаблон.
"""

import json
import tempfile
import unittest
from pathlib import Path

import pko.llm.client as client_module
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.schema import EvidenceRef, ItemVerdict, PlanItem, ProgressModel
from pko.versioning.diff import StageDelta, VersionComparison
from pko.versioning.interpret import interpret_comparison

_SPEC = ModelSpec(role="reporter", base_url="https://stub.local/v1", model="stub-model", api_key="x")


def _verdict(item_id: str, canonical_stage_id: str, status: str = "DONE",
             evidence: list[EvidenceRef] | None = None) -> ItemVerdict:
    return ItemVerdict(item_id=item_id, status=status, explanation="комментарий агента",
                        evidence=evidence or [], canonical_stage_id=canonical_stage_id)


def _model(*verdicts: ItemVerdict) -> ProgressModel:
    items = {v.item_id: PlanItem(id=v.item_id, title=f"Этап {v.canonical_stage_id}", source_slide=1)
             for v in verdicts}
    return ProgressModel(items=items, verdicts=list(verdicts))


def _comparison() -> VersionComparison:
    delta = StageDelta(
        canonical_stage_id="cs1", title="Сборка предложения",
        previous_status="NOT_STARTED", current_status="PARTIAL",
        previous_readiness=0, current_readiness=55, readiness_delta=55,
        change_type="IMPROVED",
    )
    return VersionComparison(readiness_before=0.0, readiness_after=0.5, readiness_delta=0.5,
                              stage_deltas=[delta])


class InterpretComparisonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._original_cache_dir = client_module.DEFAULT_CACHE_DIR
        client_module.DEFAULT_CACHE_DIR = Path(self.tmp.name) / "llm-cache"
        self.addCleanup(setattr, client_module, "DEFAULT_CACHE_DIR", self._original_cache_dir)

        self._original_request = ChatClient._request
        self.addCleanup(setattr, ChatClient, "_request", self._original_request)

        self.from_model = _model(_verdict("a", "cs1", status="NOT_STARTED"))
        self.to_model = _model(_verdict("a2", "cs1", status="PARTIAL"))

    def _script(self, content: str):
        def _request(self, method, path, payload):
            return {"choices": [{"message": {"content": content}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
        ChatClient._request = _request

    def test_no_spec_returns_empty_with_note(self):
        result = interpret_comparison(_comparison(), self.from_model, self.to_model, None)
        self.assertEqual(result.source, "none")
        self.assertEqual(result.progress_summary, "")
        self.assertTrue(result.notes)

    def test_no_stage_deltas_returns_empty_with_note(self):
        empty = VersionComparison(readiness_before=0, readiness_after=0, readiness_delta=0)
        result = interpret_comparison(empty, self.from_model, self.to_model, _SPEC)
        self.assertEqual(result.source, "none")

    def test_valid_interpretation_is_parsed_and_applied(self):
        self._script(json.dumps({
            "progress_summary": "За период этап продвинулся до частичной готовности.",
            "stage_business_deltas": {"cs1": "Реализован базовый сценарий."},
            "risks": [{"text": "Персонализация не завершена.", "state": "NEW"}],
            "next_focus": ["Завершить персонализацию."],
        }))
        result = interpret_comparison(_comparison(), self.from_model, self.to_model, _SPEC)
        self.assertEqual(result.source, "llm")
        self.assertEqual(result.progress_summary, "За период этап продвинулся до частичной готовности.")
        self.assertEqual(result.stage_business_deltas, {"cs1": "Реализован базовый сценарий."})
        self.assertEqual(result.current_risks, [{"text": "Персонализация не завершена.", "state": "NEW"}])
        self.assertEqual(result.next_focus, ["Завершить персонализацию."])

    def test_business_delta_for_unknown_stage_id_is_dropped(self):
        self._script(json.dumps({
            "stage_business_deltas": {"cs1": "ок", "cs_doesnotexist": "не должно попасть в ответ"},
        }))
        result = interpret_comparison(_comparison(), self.from_model, self.to_model, _SPEC)
        self.assertEqual(result.stage_business_deltas, {"cs1": "ок"})

    def test_risk_with_unknown_state_is_dropped(self):
        self._script(json.dumps({"risks": [{"text": "х", "state": "MAYBE"}]}))
        result = interpret_comparison(_comparison(), self.from_model, self.to_model, _SPEC)
        self.assertEqual(result.current_risks, [])

    def test_invalid_json_is_rejected(self):
        self._script("не json")
        result = interpret_comparison(_comparison(), self.from_model, self.to_model, _SPEC)
        self.assertEqual(result.source, "none")
        self.assertTrue(result.notes)

    def test_text_naming_an_unverified_path_is_rejected_entirely(self):
        self._script(json.dumps({"progress_summary": "См. backend/src/hidden.py для деталей."}))
        result = interpret_comparison(_comparison(), self.from_model, self.to_model, _SPEC)
        self.assertEqual(result.source, "none")
        self.assertEqual(result.progress_summary, "")

    def test_text_naming_a_verified_path_is_kept(self):
        verified = _verdict("a2", "cs1", status="PARTIAL",
                             evidence=[EvidenceRef(path="backend/src/offer.py", line=1, basis="x",
                                                    verified=True, reason="")])
        to_model = _model(verified)
        self._script(json.dumps({"progress_summary": "См. backend/src/offer.py для деталей."}))
        result = interpret_comparison(_comparison(), self.from_model, to_model, _SPEC)
        self.assertEqual(result.source, "llm")
        self.assertIn("backend/src/offer.py", result.progress_summary)


if __name__ == "__main__":
    unittest.main()
