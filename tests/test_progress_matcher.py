"""Агентный matcher: изолированный цикл на пункт плана, инструменты вместо
заранее собранного списка кандидатов.

Транспорт подменяется тем же способом, что и в остальных LLM-тестах:
`ChatClient._request` отдаёт по одному заготовленному ответу на каждый ход
цикла. Путь/строка/якорь проверяются на настоящей фикстуре `mini_repo`.
"""

import json
import unittest

from fixture_support import ensure_fixture
from pko.extractors.base import Tree
from pko.git.repo import GitRepo
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.matcher import (
    DEFAULT_MAX_STEPS,
    find_unclaimed_paths,
    match_plan,
)
from pko.progress.schema import EvidenceRef, ItemVerdict, PlanItem

SPEC = ModelSpec(role="matcher", base_url="https://stub.local/v1", model="stub-model", api_key="x")

ITEMS = [
    PlanItem(id="tasks-api", title="Постановка задач в обработку", source_slide=1),
]


def _call(name: str, args: dict, call_id: str | None = None) -> dict:
    return {
        "id": call_id or f"call_{name}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def tool_call(name: str, **args) -> dict:
    """Один ход с одним вызовом инструмента — нативный `tool_calls`."""
    return {"content": None, "tool_calls": [_call(name, args)]}


def parallel_tool_calls(*calls: tuple[str, dict]) -> dict:
    """Один ход с несколькими вызовами инструментов разом."""
    return {"content": None, "tool_calls": [
        _call(name, args, call_id=f"call_{i}_{name}") for i, (name, args) in enumerate(calls)
    ]}


def final(status: str, explanation: str = "x", evidence: list[dict] | None = None) -> dict:
    """Финальный ход — обычный текстовый ответ, без tool_calls."""
    return {"content": json.dumps(
        {"status": status, "explanation": explanation, "evidence": evidence or []},
        ensure_ascii=False,
    )}


def malformed(text: str) -> dict:
    """Ход, который не распознать ни как вызов инструмента, ни как final."""
    return {"content": text}


def scripted(*answers: dict):
    queue = list(answers)

    def _request(self, method, path, payload):
        message = queue.pop(0) if queue else final("UNCLEAR", "очередь ответов закончилась")
        return {"choices": [{"message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return _request


class MatcherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = GitRepo(ensure_fixture())
        cls.sha = cls.repo.resolve("master")
        cls.tree = Tree.at(cls.repo, cls.sha)

    def setUp(self):
        self._original = ChatClient._request
        self.addCleanup(setattr, ChatClient, "_request", self._original)
        self.client = ChatClient(spec=SPEC, use_cache=False)

    def _run(self, *answers, items=ITEMS, max_steps=DEFAULT_MAX_STEPS):
        ChatClient._request = scripted(*answers)
        return match_plan(items, self.tree, SPEC, client=self.client, max_steps=max_steps)

    def test_no_spec_returns_empty_with_note(self):
        result = match_plan(ITEMS, self.tree, spec=None)
        self.assertFalse(result.usable)
        self.assertIn("не настроен", result.notes[0])

    def test_no_items_returns_empty_with_note(self):
        result = match_plan([], self.tree, SPEC, client=self.client)
        self.assertFalse(result.usable)

    def test_item_confirmed_via_list_then_read(self):
        result = self._run(
            tool_call("list_files", glob="*.py"),
            tool_call("read_file", path="backend/src/api/v1/router.py"),
            final("DONE", "Эндпоинт постановки задачи реализован.", [
                {"path": "backend/src/api/v1/router.py", "line": 7,
                 "basis": "функция start_task ставит задачу в обработку"},
            ]),
        )
        self.assertEqual(result.source, "llm")
        verdict = result.verdicts[0]
        self.assertEqual(verdict.status, "DONE")
        self.assertTrue(verdict.is_grounded)
        self.assertTrue(verdict.evidence[0].verified)

    def test_item_not_found_after_search(self):
        result = self._run(
            tool_call("search", pattern="billing"),
            tool_call("search", pattern="subscription"),
            final("NOT_STARTED", "Кода для биллинга подписок не нашлось.", []),
        )
        verdict = result.verdicts[0]
        self.assertEqual(verdict.status, "NOT_STARTED")
        self.assertEqual(verdict.evidence, [])

    def test_fabricated_evidence_path_is_not_verified_but_kept(self):
        result = self._run(
            final("DONE", "Похоже, сделано.", [
                {"path": "backend/src/billing_service.py", "line": 1, "basis": "x"},
            ]),
        )
        verdict = result.verdicts[0]
        self.assertEqual(len(verdict.evidence), 1)
        self.assertFalse(verdict.evidence[0].verified)
        self.assertFalse(verdict.is_grounded)
        self.assertIn("без единой подтверждённой ссылки", " ".join(result.notes))

    def test_explanation_naming_a_nonexistent_file_is_rejected(self):
        result = self._run(
            final("DONE", "Также реализовано в billing_service.py.", [
                {"path": "backend/src/api/v1/router.py", "line": 7,
                 "basis": "функция start_task ставит задачу в обработку"},
            ]),
        )
        verdict = result.verdicts[0]
        self.assertIn("отклонено сторожем", verdict.explanation)
        self.assertTrue(verdict.evidence[0].verified)

    def test_stops_after_repeated_identical_tool_call(self):
        result = self._run(
            tool_call("search", pattern="x"),
            tool_call("search", pattern="x"),
            tool_call("search", pattern="x"),
            final("DONE", "не должно быть достигнуто"),
        )
        verdict = result.verdicts[0]
        self.assertEqual(verdict.status, "UNCLEAR")
        self.assertIn("одинаковых вызова", " ".join(result.notes))

    def test_stops_at_step_budget(self):
        result = self._run(
            tool_call("list_files"),
            tool_call("read_file", path="backend/src/api/v1/router.py"),
            tool_call("search", pattern="a"),
            max_steps=3,
        )
        verdict = result.verdicts[0]
        self.assertEqual(verdict.status, "UNCLEAR")
        self.assertIn("бюджет шагов", " ".join(result.notes))

    def test_tolerates_a_few_malformed_responses_then_recovers(self):
        result = self._run(
            malformed("извините, не могу помочь"),
            malformed("и снова не JSON"),
            final("NOT_STARTED", "не нашлось"),
        )
        verdict = result.verdicts[0]
        self.assertEqual(verdict.status, "NOT_STARTED")

    def test_gives_up_after_too_many_malformed_responses(self):
        result = self._run(malformed("x"), malformed("y"), malformed("z"), malformed("w"))
        verdict = result.verdicts[0]
        self.assertEqual(verdict.status, "UNCLEAR")
        self.assertIn("неразбираемый ответ", " ".join(result.notes))

    def test_parallel_tool_calls_in_one_turn_both_execute(self):
        result = self._run(
            parallel_tool_calls(
                ("list_files", {"glob": "*.py"}),
                ("search", {"pattern": "start_task"}),
            ),
            final("DONE", "Найдено сразу двумя путями.", [
                {"path": "backend/src/api/v1/router.py", "line": 7, "basis": "start_task"},
            ]),
        )
        verdict = result.verdicts[0]
        self.assertEqual(verdict.status, "DONE")
        self.assertTrue(verdict.evidence[0].verified)

    def test_invalid_tool_arguments_json_does_not_crash_and_lets_model_recover(self):
        result = self._run(
            {"content": None, "tool_calls": [{
                "id": "call_bad",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{not valid json"},
            }]},
            final("NOT_STARTED", "аргументы были битые, но цикл продолжился"),
        )
        verdict = result.verdicts[0]
        self.assertEqual(verdict.status, "NOT_STARTED")

    def test_unrecognized_status_becomes_unclear(self):
        result = self._run(final("MOSTLY_DONE"))
        self.assertEqual(result.verdicts[0].status, "UNCLEAR")

    def test_missing_tool_falls_back_to_a_clean_tool_error_not_a_crash(self):
        result = self._run(
            tool_call("delete_repo"),
            final("UNCLEAR", "инструмент не сработал"),
        )
        self.assertEqual(result.verdicts[0].status, "UNCLEAR")

    def test_multiple_items_each_get_their_own_turn_sequence(self):
        items = [
            PlanItem(id="tasks-api", title="Постановка задач", source_slide=1),
            PlanItem(id="billing", title="Биллинг подписок", source_slide=1),
        ]
        result = self._run(
            final("DONE", "Реализовано.", [
                {"path": "backend/src/api/v1/router.py", "line": 7, "basis": "start_task"},
            ]),
            final("NOT_STARTED", "Не нашлось.", []),
            items=items,
        )
        self.assertEqual(len(result.verdicts), 2)
        by_id = {v.item_id: v for v in result.verdicts}
        self.assertEqual(by_id["tasks-api"].status, "DONE")
        self.assertEqual(by_id["billing"].status, "NOT_STARTED")


class UnclaimedPathsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo = GitRepo(ensure_fixture())
        sha = repo.resolve("master")
        cls.tree = Tree.at(repo, sha)
        from pko.extractors.runner import extract_all

        cls.extraction = extract_all(cls.tree)

    def test_paths_without_verified_evidence_are_reported_as_unclaimed(self):
        verdict_with_evidence = ItemVerdict(item_id="tasks-api", status="DONE", explanation="x")
        verdict_with_evidence.evidence.append(
            EvidenceRef(path="backend/src/api/v1/router.py", line=7,
                       basis="start_task", verified=True, reason="ok")
        )
        groups = find_unclaimed_paths(self.extraction, [verdict_with_evidence])
        claimed_paths = {p for g in groups for p in g.example_paths}
        self.assertNotIn("backend/src/api/v1/router.py", claimed_paths)
        self.assertGreater(len(groups), 0)

    def test_no_verdicts_means_everything_is_unclaimed(self):
        groups = find_unclaimed_paths(self.extraction, [])
        total_files = sum(g.file_count for g in groups)
        self.assertGreater(total_files, 0)


if __name__ == "__main__":
    unittest.main()
