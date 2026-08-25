"""Роль matcher: JSON-вердикты по пунктам плана, проверка evidence по коду.

Транспорт подменяется как в `test_progress_plan_extract.py`; путь/строка/якорь
проверяются на настоящей фикстуре `mini_repo` — той же, что использует
остальной набор тестов PKO.
"""

import json
import unittest

from fixture_support import ensure_fixture
from pko.extractors.base import Tree
from pko.extractors.runner import extract_all
from pko.git.repo import GitRepo
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.matcher import find_unclaimed_paths, match_plan
from pko.progress.schema import ItemVerdict, PlanItem

SPEC = ModelSpec(role="matcher", base_url="https://stub.local/v1", model="stub-model", api_key="x")

ITEMS = [
    PlanItem(id="tasks-api", title="API постановки задач", source_slide=1),
    PlanItem(id="billing", title="Биллинг подписок", source_slide=1),
]


def scripted(*answers: str):
    queue = list(answers)

    def _request(self, method, path, payload):
        text = queue.pop(0) if queue else json.dumps({"verdicts": []})
        return {"choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return _request


class MatcherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = GitRepo(ensure_fixture())
        cls.sha = cls.repo.resolve("master")
        cls.tree = Tree.at(cls.repo, cls.sha)
        cls.extraction = extract_all(cls.tree)

    def setUp(self):
        self._original = ChatClient._request
        self.addCleanup(setattr, ChatClient, "_request", self._original)
        self.client = ChatClient(spec=SPEC, use_cache=False)

    def _run(self, response_text: str, items=ITEMS):
        ChatClient._request = scripted(response_text)
        return match_plan(items, self.extraction, self.tree, SPEC, client=self.client)

    def test_no_spec_returns_empty_with_note(self):
        result = match_plan(ITEMS, self.extraction, self.tree, spec=None)
        self.assertFalse(result.usable)
        self.assertIn("не настроен", result.notes[0])

    def test_no_items_returns_empty_with_note(self):
        result = match_plan([], self.extraction, self.tree, SPEC, client=self.client)
        self.assertFalse(result.usable)

    def test_verified_evidence_is_grounded(self):
        response = json.dumps({"verdicts": [
            {"item_id": "tasks-api", "status": "DONE",
             "explanation": "Эндпоинт постановки задачи реализован.",
             "evidence": [{"path": "backend/src/api/v1/router.py", "line": 7,
                           "basis": "функция start_task ставит задачу в обработку"}]},
            {"item_id": "billing", "status": "NOT_STARTED", "explanation": "Кода не найдено.",
             "evidence": []},
        ]})
        result = self._run(response)
        self.assertEqual(result.source, "llm")
        self.assertEqual(len(result.verdicts), 2)

        tasks_verdict = next(v for v in result.verdicts if v.item_id == "tasks-api")
        self.assertEqual(tasks_verdict.status, "DONE")
        self.assertTrue(tasks_verdict.is_grounded)
        self.assertTrue(tasks_verdict.evidence[0].verified)

        billing_verdict = next(v for v in result.verdicts if v.item_id == "billing")
        self.assertEqual(billing_verdict.status, "NOT_STARTED")
        self.assertEqual(billing_verdict.evidence, [])

    def test_evidence_with_wrong_anchor_is_not_verified_but_kept(self):
        # basis не содержит ни одного слова, реально стоящего у указанной строки —
        # ссылка остаётся в отчёте, но не подтверждена.
        response = json.dumps({"verdicts": [
            {"item_id": "tasks-api", "status": "DONE", "explanation": "Похоже, сделано.",
             "evidence": [{"path": "backend/src/api/v1/router.py", "line": 7,
                           "basis": "полностью не связанное с кодом основание xyz"}]},
        ]})
        result = self._run(response)
        verdict = result.verdicts[0]
        self.assertFalse(verdict.evidence[0].verified)
        self.assertFalse(verdict.is_grounded)
        self.assertIn("без единой подтверждённой ссылки", " ".join(result.notes))

    def test_evidence_with_nonexistent_path_is_not_verified(self):
        response = json.dumps({"verdicts": [
            {"item_id": "tasks-api", "status": "DONE", "explanation": "x",
             "evidence": [{"path": "backend/src/does_not_exist.py", "line": 1, "basis": "x"}]},
        ]})
        result = self._run(response)
        self.assertFalse(result.verdicts[0].evidence[0].verified)
        self.assertIn("нет на этом коммите", result.verdicts[0].evidence[0].reason)

    def test_verdict_for_unknown_item_id_is_dropped(self):
        response = json.dumps({"verdicts": [
            {"item_id": "ghost-item", "status": "DONE", "explanation": "x", "evidence": []},
            {"item_id": "tasks-api", "status": "NOT_STARTED", "explanation": "x", "evidence": []},
        ]})
        result = self._run(response)
        ids = {v.item_id for v in result.verdicts}
        self.assertIn("tasks-api", ids)
        self.assertNotIn("ghost-item", ids)

    def test_missing_verdict_for_known_item_becomes_unclear(self):
        response = json.dumps({"verdicts": [
            {"item_id": "tasks-api", "status": "DONE", "explanation": "x", "evidence": []},
        ]})
        result = self._run(response)
        billing_verdict = next(v for v in result.verdicts if v.item_id == "billing")
        self.assertEqual(billing_verdict.status, "UNCLEAR")

    def test_invalid_status_value_drops_the_verdict(self):
        response = json.dumps({"verdicts": [
            {"item_id": "tasks-api", "status": "MOSTLY_DONE", "explanation": "x", "evidence": []},
        ]})
        result = self._run(response)
        # Отброшен -> tasks-api тоже уйдёт в "нет вердикта" -> UNCLEAR.
        verdict = next(v for v in result.verdicts if v.item_id == "tasks-api")
        self.assertEqual(verdict.status, "UNCLEAR")

    def test_non_json_response_returns_empty_with_note(self):
        result = self._run("не могу помочь")
        self.assertFalse(result.usable)


class UnclaimedPathsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo = GitRepo(ensure_fixture())
        sha = repo.resolve("master")
        cls.tree = Tree.at(repo, sha)
        cls.extraction = extract_all(cls.tree)

    def test_paths_without_verified_evidence_are_reported_as_unclaimed(self):
        verdict_with_evidence = ItemVerdict(
            item_id="tasks-api", status="DONE", explanation="x",
            evidence=[],
        )
        from pko.progress.schema import EvidenceRef

        verdict_with_evidence.evidence.append(
            EvidenceRef(path="backend/src/api/v1/router.py", line=7,
                       basis="start_task", verified=True, reason="ok")
        )
        groups = find_unclaimed_paths(self.extraction, [verdict_with_evidence])
        claimed_paths = {p for g in groups for p in g.example_paths}
        self.assertNotIn("backend/src/api/v1/router.py", claimed_paths)
        # Что-то за пределами router.py в этой фикстуре точно есть.
        self.assertGreater(len(groups), 0)

    def test_no_verdicts_means_everything_is_unclaimed(self):
        groups = find_unclaimed_paths(self.extraction, [])
        total_files = sum(g.file_count for g in groups)
        self.assertGreater(total_files, 0)


if __name__ == "__main__":
    unittest.main()
