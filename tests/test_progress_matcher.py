"""Единый агент (`run_agent`): текст слайдов -> пункты плана + вердикты, по
одному пункту за раз через `submit_verdict`, до `finish`.

Транспорт подменяется тем же способом, что и в остальных LLM-тестах:
`ChatClient._request` отдаёт по одному заготовленному ответу на каждый ход
сессии. Путь/строка/якорь проверяются на настоящей фикстуре `mini_repo`.
"""

import json
import unittest

from fixture_support import ensure_fixture
from pko.extractors.base import Tree
from pko.git.repo import GitRepo
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.matcher import DEFAULT_MAX_STEPS, find_unclaimed_paths, run_agent
from pko.progress.pptx_reader import Slide, SlideShape
from pko.progress.schema import EvidenceRef, ItemVerdict

SPEC = ModelSpec(role="matcher", base_url="https://stub.local/v1", model="stub-model", api_key="x")

SLIDES = [
    Slide(number=1, heading="Задачи спринта", shapes=[
        SlideShape(text="Постановка задач в обработку\n"
                        "API, который принимает задачу и ставит её в очередь",
                   left=0.5, top=1.2, width=4.0, height=2.2),
        SlideShape(text="Биллинг подписок\nСписание средств за подписку по расписанию",
                   left=4.8, top=1.2, width=4.0, height=2.2),
    ]),
]

ROUTER_EVIDENCE = [{"path": "backend/src/api/v1/router.py", "line": 7,
                    "basis": "функция start_task ставит задачу в обработку"}]


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


def submit(item_id: str, title: str, source_slide: int, status: str,
           explanation: str = "x", evidence: list[dict] | None = None, **extra) -> dict:
    args = {"item_id": item_id, "title": title, "source_slide": source_slide,
            "status": status, "explanation": explanation, "evidence": evidence or [], **extra}
    return tool_call("submit_verdict", **args)


def finish() -> dict:
    return tool_call("finish")


def malformed(text: str) -> dict:
    """Ход без вызова инструмента вообще — единственный вид «неразобранного» хода теперь."""
    return {"content": text}


def scripted(*answers: dict):
    queue = list(answers)

    def _request(self, method, path, payload):
        message = queue.pop(0) if queue else finish()
        return {"choices": [{"message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return _request


class MatcherAgentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = GitRepo(ensure_fixture())
        cls.sha = cls.repo.resolve("master")
        cls.tree = Tree.at(cls.repo, cls.sha)

    def setUp(self):
        self._original = ChatClient._request
        self.addCleanup(setattr, ChatClient, "_request", self._original)
        self.client = ChatClient(spec=SPEC, use_cache=False)

    def _run(self, *answers, slides=SLIDES, max_steps=DEFAULT_MAX_STEPS, on_verdict=None):
        ChatClient._request = scripted(*answers)
        return run_agent(
            slides, self.tree, SPEC, client=self.client, max_steps=max_steps, on_verdict=on_verdict
        )

    def test_no_spec_returns_empty_with_note(self):
        result = run_agent(SLIDES, self.tree, spec=None)
        self.assertFalse(result.usable)
        self.assertIn("не настроен", result.notes[0])

    def test_no_content_slides_returns_empty_with_note(self):
        empty_slide = Slide(number=1, heading=None, shapes=[])
        result = run_agent([empty_slide], self.tree, SPEC, client=self.client)
        self.assertFalse(result.usable)
        self.assertIn("нет текстовых фигур", result.notes[0])

    def test_full_session_submit_then_finish(self):
        result = self._run(
            tool_call("list_files", glob="*.py"),
            tool_call("read_file", path="backend/src/api/v1/router.py"),
            submit("tasks-api", "Постановка задач в обработку", 1, "DONE",
                  "Эндпоинт постановки задачи реализован.", ROUTER_EVIDENCE),
            submit("billing", "Биллинг подписок", 1, "NOT_STARTED",
                  "Кода для биллинга подписок не нашлось."),
            finish(),
        )
        self.assertEqual(result.source, "llm")
        self.assertEqual(len(result.items), 2)
        by_id = {v.item_id: v for v in result.verdicts}
        self.assertEqual(by_id["tasks-api"].status, "DONE")
        self.assertTrue(by_id["tasks-api"].is_grounded)
        self.assertTrue(by_id["tasks-api"].evidence[0].verified)
        self.assertEqual(by_id["billing"].status, "NOT_STARTED")

    def test_invalid_submit_is_rejected_and_can_be_resubmitted(self):
        result = self._run(
            submit("ghost", "Выдуманная задача", 99, "DONE"),
            submit("tasks-api", "Постановка задач", 1, "NOT_STARTED"),
            finish(),
        )
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.items[0].id, "tasks-api")

    def test_on_verdict_fires_once_per_accepted_submission_only(self):
        seen: list[tuple[str, str]] = []
        self._run(
            submit("ghost", "Выдуманная задача", 99, "DONE"),
            tool_call("list_files", glob="*.py"),
            submit("tasks-api", "Постановка задач в обработку", 1, "DONE",
                  "Эндпоинт постановки задачи реализован.", ROUTER_EVIDENCE),
            submit("billing", "Биллинг подписок", 1, "NOT_STARTED",
                  "Кода для биллинга подписок не нашлось."),
            finish(),
            on_verdict=lambda item, verdict: seen.append((item.id, verdict.status)),
        )
        # "ghost" (source_slide=99, неизвестный слайд) отклонён — колбэк по
        # нему не зовётся; list_files — не submit_verdict, тоже мимо колбэка.
        self.assertEqual(seen, [("tasks-api", "DONE"), ("billing", "NOT_STARTED")])

    def test_missing_item_id_gets_generated(self):
        result = self._run(
            tool_call("submit_verdict", title="Без id", source_slide=1,
                      status="NOT_STARTED", explanation="x", evidence=[]),
            finish(),
        )
        self.assertEqual(len(result.items), 1)
        self.assertTrue(result.items[0].id)

    def test_resubmitting_same_item_id_updates_the_verdict(self):
        result = self._run(
            submit("tasks-api", "Постановка задач", 1, "NOT_STARTED", "первая попытка"),
            submit("tasks-api", "Постановка задач", 1, "DONE", "нашёл после доп. поиска", ROUTER_EVIDENCE),
            finish(),
        )
        self.assertEqual(len(result.items), 1)
        self.assertEqual(len(result.verdicts), 1)
        self.assertEqual(result.verdicts[0].status, "DONE")

    def test_progress_is_clamped_to_0_100(self):
        result = self._run(
            submit("over", "Пункт с большим progress", 1, "PARTIAL", "x", progress=150),
            submit("under", "Пункт с отрицательным progress", 1, "PARTIAL", "x", progress=-5),
            submit("missing", "Пункт без progress", 1, "PARTIAL", "x"),
            finish(),
        )
        by_id = {v.item_id: v for v in result.verdicts}
        self.assertEqual(by_id["over"].progress, 100)
        self.assertEqual(by_id["under"].progress, 0)
        self.assertEqual(by_id["missing"].progress, 0)

    def test_fabricated_evidence_path_is_not_verified_but_kept(self):
        result = self._run(
            submit("tasks-api", "Постановка задач", 1, "DONE", "Похоже, сделано.", [
                {"path": "backend/src/billing_service.py", "line": 1, "basis": "x"},
            ]),
            finish(),
        )
        verdict = result.verdicts[0]
        self.assertEqual(len(verdict.evidence), 1)
        self.assertFalse(verdict.evidence[0].verified)
        self.assertFalse(verdict.is_grounded)
        self.assertIn("без единой подтверждённой ссылки", " ".join(result.notes))

    def test_explanation_naming_a_nonexistent_file_is_rejected(self):
        result = self._run(
            submit("tasks-api", "Постановка задач", 1, "DONE",
                  "Также реализовано в billing_service.py.", ROUTER_EVIDENCE),
            finish(),
        )
        verdict = result.verdicts[0]
        self.assertIn("отклонено сторожем", verdict.explanation)
        self.assertTrue(verdict.evidence[0].verified)

    def test_stops_after_repeated_identical_tool_call(self):
        result = self._run(
            tool_call("search", pattern="x"),
            tool_call("search", pattern="x"),
            tool_call("search", pattern="x"),
            finish(),
        )
        self.assertFalse(result.usable)
        self.assertIn("одинаковых вызова", " ".join(result.notes))

    def test_stops_at_step_budget(self):
        result = self._run(
            tool_call("list_files"),
            tool_call("read_file", path="backend/src/api/v1/router.py"),
            tool_call("search", pattern="a"),
            max_steps=3,
        )
        self.assertFalse(result.usable)
        self.assertIn("бюджет шагов", " ".join(result.notes))

    def test_budget_exhausted_still_returns_already_submitted_verdicts(self):
        result = self._run(
            submit("tasks-api", "Постановка задач", 1, "DONE", "готово", ROUTER_EVIDENCE),
            tool_call("search", pattern="a"),
            tool_call("search", pattern="b"),
            max_steps=3,
        )
        self.assertTrue(result.usable)
        self.assertEqual(result.verdicts[0].status, "DONE")
        self.assertIn("бюджет шагов", " ".join(result.notes))

    def test_finish_immediately_without_any_submission(self):
        result = self._run(finish())
        self.assertFalse(result.usable)
        self.assertIn("не отправлены", " ".join(result.notes))

    def test_tolerates_a_few_malformed_responses_then_recovers(self):
        result = self._run(
            malformed("извините, не могу помочь"),
            malformed("и снова не JSON"),
            submit("tasks-api", "Постановка задач", 1, "NOT_STARTED", "не нашлось"),
            finish(),
        )
        self.assertEqual(result.verdicts[0].status, "NOT_STARTED")

    def test_gives_up_after_too_many_malformed_responses(self):
        result = self._run(malformed("x"), malformed("y"), malformed("z"), malformed("w"))
        self.assertFalse(result.usable)
        self.assertIn("не вызвал инструмент", " ".join(result.notes))

    def test_parallel_tool_calls_in_one_turn_both_execute(self):
        result = self._run(
            parallel_tool_calls(
                ("list_files", {"glob": "*.py"}),
                ("search", {"pattern": "start_task"}),
            ),
            submit("tasks-api", "Постановка задач", 1, "DONE",
                  "Найдено сразу двумя путями.", ROUTER_EVIDENCE),
            finish(),
        )
        self.assertEqual(result.verdicts[0].status, "DONE")
        self.assertTrue(result.verdicts[0].evidence[0].verified)

    def test_submit_verdict_and_finish_in_the_same_turn(self):
        result = self._run(parallel_tool_calls(
            ("submit_verdict", {"item_id": "tasks-api", "title": "Постановка задач",
                                "source_slide": 1, "status": "DONE",
                                "explanation": "готово", "evidence": ROUTER_EVIDENCE}),
            ("finish", {}),
        ))
        self.assertTrue(result.usable)
        self.assertEqual(result.verdicts[0].status, "DONE")

    def test_invalid_tool_arguments_json_does_not_crash_and_lets_model_recover(self):
        result = self._run(
            {"content": None, "tool_calls": [{
                "id": "call_bad",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{not valid json"},
            }]},
            submit("tasks-api", "Постановка задач", 1, "NOT_STARTED", "аргументы были битые"),
            finish(),
        )
        self.assertEqual(result.verdicts[0].status, "NOT_STARTED")


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
