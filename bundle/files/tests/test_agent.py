"""Агент разведки: инструменты, верификация находок, стоп-условия, трасса.

Транспорт подменяется целиком: `ChatClient._request` возвращает заранее
заготовленные ответы. Ни один тест не ходит в сеть и не требует endpoint'а.
"""

import json
import time
import unittest
from pathlib import Path

from fixture_support import ensure_fixture
from pko.agent.loop import AgentResult, load_prompt, map_groups_to_candidates, run_scout
from pko.agent.tools import ToolBox
from pko.agent.trace_report import render_trace
from pko.agent.verify import verify_facts, verify_groups, verify_invariants
from pko.assemble.candidates import build_candidates
from pko.extractors.base import Tree
from pko.extractors.runner import extract_all
from pko.git.repo import GitRepo
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec

SPEC = ModelSpec(role="scout", base_url="https://stub.local/v1", model="stub-model", api_key="x")


def scripted(*answers: str):
    """Подменить транспорт: каждый вызов отдаёт следующий заготовленный ответ."""
    queue = list(answers)

    def _request(self, method, path, payload):
        text = queue.pop(0) if queue else json.dumps({"final": {}})
        return {"choices": [{"message": {"content": text}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    return _request


def tool_call(tool: str, **args) -> str:
    return json.dumps({"tool": tool, "args": args}, ensure_ascii=False)


class AgentTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = GitRepo(ensure_fixture())
        cls.sha = cls.repo.resolve("master")
        cls.tree = Tree.at(cls.repo, cls.sha)
        cls.extraction = extract_all(cls.tree)

    def run_agent(self, *answers, max_steps: int = 0) -> AgentResult:
        client = ChatClient(spec=SPEC, use_cache=False)
        original = ChatClient._request
        ChatClient._request = scripted(*answers)
        self.addCleanup(setattr, ChatClient, "_request", original)
        return run_scout(
            tree=self.tree, extraction=self.extraction, spec=SPEC,
            meta={"repo": "mini_repo", "commit": self.sha, "version_label": "current"},
            max_steps=max_steps, client=client,
        )


class ToolsTest(AgentTestCase):
    def setUp(self):
        self.tools = ToolBox(tree=self.tree)

    def test_malformed_final_does_not_crash_and_marks_incomplete(self):
        """`{"final": "готово"}` — не объект: прогон обязан закончиться, а не упасть."""
        result = self.run_agent(json.dumps({"final": "готово"}))
        self.assertTrue(result.trace.incomplete)
        self.assertIn("неверной формы", result.trace.stop_reason)

    def test_secrets_are_masked_in_arguments_and_dicts(self):
        from pko.agent.tools import _mask_secrets

        self.assertNotIn("sk-secret", _mask_secrets('c = Client(api_key="sk-secret-123")'))
        self.assertNotIn("sk-abc123", _mask_secrets('  "Authorization": "Bearer sk-abc123",'))
        self.assertEqual(_mask_secrets("def read_file(path, offset=1):"),
                         "def read_file(path, offset=1):")

    def test_paging_to_the_end_clears_incompleteness(self):
        """Долистанное до конца дерево полное: остаток закрыт, а не сосчитан."""
        from pko.agent import tools as tools_module

        original = tools_module.MAX_LIST_FILES
        tools_module.MAX_LIST_FILES = 2
        self.addCleanup(setattr, tools_module, "MAX_LIST_FILES", original)

        offset = 1
        pages = 0
        while True:
            page = self.tools.list_files("*", offset=offset)
            pages += 1
            if not page.meta["rest"]:
                break
            self.assertTrue(self.tools.pending_pages, msg="незакрытый хвост должен быть виден")
            offset += page.meta["shown"]
        self.assertGreater(pages, 1, msg="дерево фикстуры должно занять больше одной страницы")
        self.assertEqual(self.tools.pending_pages, {},
                         msg="после полного обхода незакрытых хвостов нет")

    def test_unfinished_pagination_stays_pending(self):
        from pko.agent import tools as tools_module

        original = tools_module.MAX_LIST_FILES
        tools_module.MAX_LIST_FILES = 2
        self.addCleanup(setattr, tools_module, "MAX_LIST_FILES", original)

        self.tools.list_files("*")
        self.assertEqual(list(self.tools.pending_pages), ["list:*"])

    def test_search_paginates_at_the_hit_limit(self):
        """Упереться в потолок совпадений и промолчать значит соврать о полноте."""
        from pko.agent import tools as tools_module

        original = tools_module.MAX_SEARCH_HITS
        tools_module.MAX_SEARCH_HITS = 2
        self.addCleanup(setattr, tools_module, "MAX_SEARCH_HITS", original)

        first = self.tools.search("import", glob="*.py")
        self.assertGreater(first.meta["rest"], 0)
        self.assertIn("offset=", first.content)
        self.assertEqual(list(self.tools.pending_pages), ["search:import|*.py"])

        offset = 1 + first.meta["hits"]
        while True:
            page = self.tools.search("import", glob="*.py", offset=offset)
            if not page.meta["rest"]:
                break
            offset += page.meta["hits"]
        self.assertEqual(self.tools.pending_pages, {})

    def test_catastrophic_pattern_is_bounded_by_time(self):
        """Откат нельзя прервать изнутри `re`, поэтому шаблон уходит в отдельный процесс."""
        from pko.agent import tools as tools_module

        original = tools_module.SEARCH_SECONDS
        tools_module.SEARCH_SECONDS = 1.0
        self.addCleanup(setattr, tools_module, "SEARCH_SECONDS", original)

        class OneBadFile:
            files = ["big.py"]

            def read(self, path):
                return "a" * 4000

        box = tools_module.ToolBox(tree=OneBadFile())
        started = time.perf_counter()
        result = box.search("(a|a)+b")
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 15, msg=f"поиск не был прерван: {elapsed:.1f} с")
        self.assertTrue(result.meta["timed_out"])
        self.assertIn("таймауту", result.content)
        self.assertEqual(box.timed_out_searches, 1,
                         msg="снятый по таймауту поиск делает обход неполным")

    def test_list_files_hides_vendor(self):
        result = self.tools.list_files("*.py")
        self.assertTrue(result.ok)
        self.assertIn("backend/src/config/settings.py", result.content)
        for line in result.content.splitlines():
            self.assertNotIn("__pycache__", line)
            self.assertNotIn("node_modules", line)

    def test_read_file_masks_secrets(self):
        result = self.tools.read_file("backend/src/config/settings.py")
        self.assertTrue(result.ok)
        self.assertIn("llm_api_key", result.content)
        self.assertNotIn("секрет-не-должен-попасть", result.content)
        self.assertIn("<скрыто>", result.content)

    def test_env_file_is_never_returned(self):
        result = self.tools.read_file(".env")
        self.assertFalse(result.ok)
        self.assertIn("ключи", result.content)

    def test_read_file_reports_missing_path(self):
        self.assertFalse(self.tools.read_file("нет-такого.py").ok)

    def test_search_returns_path_and_line(self):
        result = self.tools.search("add_node", "*.py")
        self.assertTrue(result.ok)
        self.assertIn("backend/src/agent/graph.py:", result.content)

    def test_note_fact_rejects_unknown_kind(self):
        self.assertFalse(self.tools.note_fact("ВЫДУМКА", "что-то", "a.py", 1).ok)
        self.assertEqual(self.tools.facts, [])

    def test_unknown_tool_is_reported(self):
        result = self.tools.call("bash", {"cmd": "ls"})
        self.assertFalse(result.ok)
        self.assertIn("неизвестный инструмент", result.content)


class VerifyTest(AgentTestCase):
    def test_valid_fact_is_accepted(self):
        facts, verdicts = verify_facts([{
            "kind": "GRAPH_NODE", "claim": "узел графа search_schema",
            "path": "backend/src/agent/graph.py", "line": 7,
        }], self.tree)
        self.assertEqual(len(facts), 1)
        self.assertTrue(verdicts[0].ok)
        self.assertEqual(facts[0].kind, "GRAPH_NODE")

    def test_sql_write_without_write_statement_is_rejected(self):
        """Тип, меняющий вердикт Gate, нельзя подтвердить одним совпадением слова.

        В фикстуре `execute` выполняет SELECT. Назвав его записью в БД, агент
        уронил бы проверку «только чтение» — поэтому нужен признак в коде.
        """
        _, verdicts = verify_facts([{
            "kind": "SQL_WRITE", "claim": "функция execute пишет в базу",
            "path": "backend/src/db_tools/executor.py", "line": 9,
        }], self.tree)
        self.assertFalse(verdicts[0].ok)
        self.assertIn("влияет на вердикт Gate", verdicts[0].reason)

    def test_generic_words_do_not_prove_a_gate_kind(self):
        """Слово в комментарии, аннотация и имя переменной — не конструкция."""
        cases = [
            ("GRAPH_EDGE", "аннотация возвращаемого типа"),
            ("GRAPH_NODE", "слово в комментарии"),
            ("ROUTE", "слово в имени переменной"),
        ]
        lines = {
            "GRAPH_EDGE": ["def handler(x: int) -> None:", "    return None"],
            "GRAPH_NODE": ["# сюда можно было бы добавить node", "value = 1"],
            "ROUTE": ["route_prefix = '/api'", "value = 2"],
        }
        from pko.agent.verify import _kind_mismatch

        for kind, what in cases:
            with self.subTest(kind=kind, case=what):
                self.assertTrue(_kind_mismatch(kind, lines[kind], 1),
                                msg=f"{what} не должно подтверждать {kind}")

    def test_real_constructions_still_pass(self):
        from pko.agent.verify import _kind_mismatch

        real = {
            "GRAPH_NODE": ["workflow.add_node('search', run_search)"],
            "GRAPH_EDGE": ["workflow.add_edge('search', 'answer')"],
            "ROUTE": ["@app.post('/tasks')", "def create_task(): ..."],
        }
        for kind, lines in real.items():
            with self.subTest(kind=kind):
                self.assertEqual(_kind_mismatch(kind, lines, 1), "")

    def test_sql_read_with_select_is_accepted(self):
        facts, verdicts = verify_facts([{
            "kind": "SQL_READ", "claim": "чтение hr_headcount запросом SELECT",
            "path": "backend/src/db_tools/executor.py", "line": 3,
        }], self.tree)
        self.assertTrue(verdicts[0].ok, msg=verdicts[0].reason)
        self.assertEqual(len(facts), 1)

    def test_line_outside_file_is_rejected(self):
        _, verdicts = verify_facts([{
            "kind": "ROUTE", "claim": "эндпоинт",
            "path": "backend/src/agent/graph.py", "line": 99999,
        }], self.tree)
        self.assertFalse(verdicts[0].ok)
        self.assertIn("вне файла", verdicts[0].reason)

    def test_missing_path_is_rejected(self):
        _, verdicts = verify_facts([{
            "kind": "ROUTE", "claim": "эндпоинт", "path": "выдумка.py", "line": 1,
        }], self.tree)
        self.assertFalse(verdicts[0].ok)
        self.assertIn("нет на этом коммите", verdicts[0].reason)

    def test_claim_without_anchor_is_rejected(self):
        """Ссылка верная, но в этом месте ничего похожего на утверждение нет."""
        _, verdicts = verify_facts([{
            "kind": "EXTERNAL", "claim": "обращение к Kafka через продюсер",
            "path": "backend/src/agent/graph.py", "line": 1,
        }], self.tree)
        self.assertFalse(verdicts[0].ok)
        self.assertIn("нет ни одного слова", verdicts[0].reason)

    def test_groups_require_real_paths(self):
        groups, problems = verify_groups([
            {"name": "Оркестрация", "paths": ["backend/src/agent"]},
            {"name": "Выдумка", "paths": ["нет/такого"]},
        ], self.tree)
        self.assertIn("Оркестрация", groups)
        self.assertNotIn("Выдумка", groups)
        self.assertTrue(problems)

    def test_invariant_without_support_is_rejected(self):
        accepted, rejected = verify_invariants([
            {"invariant": "Только чтение", "path": "backend/src/db_tools/executor.py"},
            {"invariant": "Ничем не подтверждено", "path": "нет/такого.py"},
        ], self.tree)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(rejected), 1)


class LoopTest(AgentTestCase):
    def test_final_collects_verified_facts_only(self):
        final = json.dumps({"final": {
            "facts": [
                {"kind": "GRAPH_NODE", "claim": "узел search_schema",
                 "path": "backend/src/agent/graph.py", "line": 7},
                {"kind": "ROUTE", "claim": "выдуманный эндпоинт",
                 "path": "нет-такого.py", "line": 1},
            ],
            "groups": [{"name": "Оркестрация графа", "paths": ["backend/src/agent"]}],
            "process_trajectory": ["план", "поиск", "синтез"],
            "guardrail_invariants": [
                {"invariant": "Только чтение данных",
                 "path": "backend/src/db_tools/executor.py", "line": 5},
            ],
        }}, ensure_ascii=False)

        result = self.run_agent(final)
        self.assertEqual(len(result.facts), 1)
        self.assertEqual(len(result.trace.rejected_facts), 1)
        self.assertIn("Оркестрация графа", result.groups)
        self.assertEqual(result.process_trajectory, ["план", "поиск", "синтез"])
        self.assertEqual(len(result.guardrail_invariants), 1)
        self.assertFalse(result.incomplete)
        self.assertEqual(result.trace.stop_reason, "агент завершил разведку")

    def test_repeated_call_stops_the_loop(self):
        repeat = tool_call("list_files", glob="*.py")
        result = self.run_agent(repeat, repeat, repeat, repeat)
        self.assertTrue(result.incomplete)
        self.assertIn("повтор", result.trace.stop_reason.lower())
        self.assertTrue(any("не завершил" in n or "финала" in n for n in result.notes))

    def test_max_steps_is_respected_when_set(self):
        call = tool_call("list_files", glob="*.py")
        other = tool_call("search", pattern="add_node", glob="*.py")
        result = self.run_agent(call, other, call, other, max_steps=2)
        self.assertTrue(result.incomplete)
        self.assertIn("лимит шагов", result.trace.stop_reason)
        self.assertEqual(len(result.trace.steps), 2)

    def test_incomplete_run_cannot_supply_gate_evidence(self):
        fact = tool_call(
            "note_fact", kind="GRAPH_NODE", claim="узел search_schema",
            path="backend/src/agent/graph.py", line=7,
        )
        result = self.run_agent(fact, max_steps=1)
        self.assertTrue(result.incomplete)
        self.assertEqual(len(result.facts), 1)
        self.assertFalse(result.facts[0].gate_eligible)
        self.assertFalse(result.trace.accepted_facts[0]["gate_eligible"])
        self.assertTrue(any("исключены из вердикта" in note for note in result.notes))

    def test_static_hints_are_not_in_the_first_request(self):
        from pko.agent.loop import _seed_message, _static_hints

        seed = _seed_message(self.tree, self.extraction)
        self.assertNotIn("facts_by_kind", seed)
        self.assertNotIn("GRAPH_NODE", seed)
        self.assertIn("facts_by_kind", _static_hints(self.extraction))

    def test_static_hints_require_independent_exploration(self):
        result = self.run_agent(
            tool_call("static_hints"),
            tool_call("list_files", glob="*.py"),
            tool_call("static_hints"),
            json.dumps({"final": {}}),
        )
        self.assertFalse(result.trace.steps[0].ok)
        self.assertIn("самостоятельно", result.trace.steps[0].result)
        self.assertTrue(result.trace.steps[2].ok)
        self.assertIn("facts_by_kind", result.trace.steps[2].result)

    def test_invalid_json_is_survived_then_stops(self):
        result = self.run_agent("не json", "тоже не json", "и снова")
        self.assertTrue(result.incomplete)
        self.assertEqual(result.trace.totals()["parse_errors"], 3)

    def test_trace_keeps_full_read_result(self):
        read = tool_call("read_file", path="backend/src/agent/graph.py", limit=50)
        result = self.run_agent(read, json.dumps({"final": {}}))
        step = result.trace.steps[0]
        self.assertEqual(step.tool, "read_file")
        self.assertIn("def build_graph", step.result)
        self.assertGreater(result.trace.bytes_read, 0)

    def test_prompt_version_and_packs_land_in_trace(self):
        """Sha считается по собранному промпту: набор паков — часть условий прогона."""
        _, version, core_sha = load_prompt()
        result = self.run_agent(json.dumps({"final": {}}))
        self.assertEqual(result.trace.prompt_version, version)

        composed_sha = load_prompt(packs=result.trace.packs)[2]
        self.assertEqual(result.trace.prompt_sha, composed_sha)
        self.assertTrue(result.trace.packs, msg="на фикстуре стек должен определиться")
        self.assertNotEqual(result.trace.prompt_sha, core_sha,
                            msg="паки обязаны менять hash, иначе прогоны несравнимы")

    def test_stack_detection_picks_packs_by_dependencies(self):
        from pko.agent.stack import detect

        profile = detect(self.tree, self.extraction)
        self.assertIn("agents", profile.packs, msg="в фикстуре есть langgraph")
        self.assertIn("data", profile.packs, msg="в фикстуре есть sqlalchemy")
        self.assertTrue(profile.reasons["agents"], msg="причина выбора должна быть названа")

    def test_trace_renders_to_html(self):
        read = tool_call("read_file", path="backend/src/agent/graph.py")
        result = self.run_agent(read, json.dumps({"final": {}}))
        html = render_trace(result.trace)
        self.assertTrue(html.startswith("<!DOCTYPE html>"))
        self.assertIn("Сводка прогона", html)
        self.assertIn("read_file", html)


class GroupMappingTest(AgentTestCase):
    def test_paths_map_to_candidate_ids(self):
        candidates = build_candidates(self.extraction)
        mapped = map_groups_to_candidates({"Оркестрация": ["backend/src/agent"]}, candidates)
        self.assertIn("Оркестрация", mapped)
        self.assertTrue(mapped["Оркестрация"])

    def test_unknown_path_yields_no_group(self):
        candidates = build_candidates(self.extraction)
        self.assertEqual(map_groups_to_candidates({"Пусто": ["нет/такого"]}, candidates), {})

    def test_file_path_matches_its_module(self):
        """Агент вправе назвать файл: модуль-кандидат представлен другим файлом пакета."""
        candidates = build_candidates(self.extraction)
        mapped = map_groups_to_candidates(
            {"Исполнение SQL": ["backend/src/db_tools/executor.py"]}, candidates
        )
        self.assertIn("Исполнение SQL", mapped)
        self.assertTrue(
            any(c.startswith("module:") for c in mapped["Исполнение SQL"]),
            msg="файл внутри пакета должен подтянуть модуль этого пакета",
        )


class DedupeTest(AgentTestCase):
    """Один факт, записанный и по ходу, и в финале, не должен считаться дважды."""

    def test_note_fact_and_final_do_not_double_count(self):
        fact = {"kind": "GRAPH_NODE", "claim": "узел search_schema",
                "path": "backend/src/agent/graph.py", "line": 7}
        result = self.run_agent(
            tool_call("note_fact", **fact),
            json.dumps({"final": {"facts": [fact], "groups": []}}, ensure_ascii=False),
        )
        self.assertEqual(len(result.facts), 1, msg="дубль должен склеиться")
        self.assertEqual(len(result.trace.accepted_facts), 1)

    def test_same_place_different_claims_are_kept(self):
        """Разные утверждения об одной строке — это разные факты."""
        base = {"path": "backend/src/agent/graph.py", "line": 7}
        result = self.run_agent(json.dumps({"final": {"facts": [
            {"kind": "GRAPH_NODE", "claim": "узел search_schema", **base},
            {"kind": "GRAPH_NODE", "claim": "узел плана plan", **base},
        ], "groups": []}}, ensure_ascii=False))
        self.assertEqual(len(result.facts), 2)

    def test_static_and_agent_fact_at_same_semantic_location_are_merged(self):
        from pko.extractors.base import Fact
        from pko.pipeline import _merge_facts

        static = Fact(
            kind="SQL_READ", key="SELECT", value="SELECT * FROM x",
            path="backend/src/db_tools/executor.py", line=5,
        )
        agent = Fact(
            kind="EFFECT", key="чтение x", value="чтение x",
            path=static.path, line=static.line, category="EFFECT", action="read",
            mechanism="sql", gate_eligible=False,
        )
        merged, duplicates = _merge_facts([static], [agent])
        self.assertEqual(merged, [static])
        self.assertEqual(duplicates, 1)

    def test_optional_action_does_not_bypass_static_dedupe(self):
        from pko.extractors.base import Fact
        from pko.pipeline import _merge_facts

        static = Fact(
            kind="ROUTE", key="POST /tasks", value="route",
            path="backend/src/api/v1/tasks.py", line=10,
        )
        agent = Fact(
            kind="ENTRYPOINT", key="POST /tasks", value="route",
            path=static.path, line=static.line, category="ENTRYPOINT",
            mechanism="http_server", gate_eligible=True,
        )
        merged, duplicates = _merge_facts([static], [agent])
        self.assertEqual(merged, [static])
        self.assertEqual(duplicates, 1)


class HistoryWindowTest(AgentTestCase):
    """История диалога обрезается, иначе каждый шаг везёт весь прочитанный код."""

    def test_window_keeps_task_and_recent_exchanges(self):
        from pko.agent.loop import HISTORY_WINDOW, _window

        messages = [{"role": "system", "content": "задача"},
                    {"role": "user", "content": "подсказки"}]
        messages += [{"role": "user", "content": f"шаг {i}"} for i in range(50)]

        sent = _window(messages)
        self.assertEqual(len(sent), HISTORY_WINDOW + 2)
        self.assertEqual(sent[0]["content"], "задача", msg="системный промпт держим всегда")
        self.assertEqual(sent[1]["content"], "подсказки", msg="подсказки разбора держим всегда")
        self.assertEqual(sent[-1]["content"], "шаг 49", msg="хвост — последние обмены")

    def test_short_history_is_untouched(self):
        from pko.agent.loop import _window

        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        self.assertEqual(_window(messages), messages)

    def test_long_run_does_not_grow_request(self):
        from pko.agent.loop import HISTORY_WINDOW

        """Двадцать шагов подряд: размер запроса выходит на плато, а не растёт."""
        calls = [tool_call("search", pattern=f"p{i}", glob="*.py") for i in range(20)]
        result = self.run_agent(*calls, json.dumps({"final": {}}))
        sizes = [s.request_messages for s in result.trace.steps if s.action == "tool"]
        self.assertGreater(len(sizes), 10)
        # Плато: 2 сообщения начала диалога + журнал обхода + окно истории.
        self.assertLessEqual(max(sizes), HISTORY_WINDOW + 3,
                             msg=f"история не обрезается: {sizes}")

    def test_request_size_plateaus_over_a_long_run(self):
        """Журнал едет в каждом запросе, поэтому расти он не должен.

        Считаем не сообщения, а символы: именно они оплачиваются и именно они
        упираются в предел контекста endpoint'а.
        """
        from pko.agent.loop import JOURNAL_BUDGET, _compact, _window

        base = [{"role": "system", "content": "задача"}, {"role": "user", "content": "подсказки"}]
        exchanges = [{"role": "user", "content": "x" * 500} for _ in range(40)]
        sizes = []
        for steps in (20, 200, 2000):
            journal = [f"#{i} search(glob=*.py, pattern=шаблон-{i}) → hits=3"
                       for i in range(1, steps + 1)]
            sent = _window(base + exchanges, journal)
            sizes.append(sum(len(m["content"]) for m in sent))

        # Разница между 200 и 2000 шагами — только ширина строки «#1–#N свёрнуто»,
        # то есть логарифм, а не линия: журнал уже упёрся в бюджет.
        self.assertLess(sizes[2] - sizes[1], 100,
                        msg=f"объём запроса растёт с числом шагов: {sizes}")
        without_journal = sum(len(m["content"]) for m in _window(base + exchanges))
        self.assertLessEqual(max(sizes) - without_journal, JOURNAL_BUDGET + 200,
                             msg=f"журнал вышел за бюджет: {sizes}")
        self.assertLessEqual(len(" ".join(_compact([f"#{i} t()" for i in range(5000)]))),
                             JOURNAL_BUDGET + 200)

    def test_compaction_keeps_the_recent_steps(self):
        from pko.agent.loop import _compact

        journal = [f"#{i} read_file(path=f{i}.py) → lines_total=10" for i in range(1, 400)]
        out = _compact(journal)
        self.assertIn("свёрнуто", out[0])
        self.assertEqual(out[-1], journal[-1], msg="последний шаг обязан остаться дословно")

    def test_journal_survives_trimming(self):
        """Вытесненные из окна шаги остаются в журнале — агент помнит, что смотрел."""
        calls = [tool_call("read_file", path="src/app.py", offset=1, limit=5) for _ in range(1)]
        calls += [tool_call("search", pattern=f"p{i}", glob="*.py") for i in range(20)]
        result = self.run_agent(*calls, json.dumps({"final": {}}))
        last = [s for s in result.trace.steps if s.action == "tool"][-1]
        journal = [m for m in last.request if "Журнал уже сделанных шагов" in m.get("preview", "")]
        self.assertEqual(len(journal), 1, msg="журнал должен быть ровно один")
        self.assertIn("#1 read_file", journal[0]["preview"],
                      msg="первое чтение вытеснено из окна, но помнится журналом")


class TraceFileTest(AgentTestCase):
    def test_trace_is_written_owner_only(self):
        import tempfile

        result = self.run_agent(json.dumps({"final": {}}))
        with tempfile.TemporaryDirectory() as raw:
            path = result.trace.save(Path(raw) / "agent_trace.json")
            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "pko-agent-trace/0.1")


class PipelineIntegrationTest(AgentTestCase):
    """Полный путь: агент → факты → кандидаты → модель → проверки → вердикт."""

    def _analyze(self, answer: str):
        from pko.history.selector import select_versions
        from pko.pipeline import analyze_version

        client = ChatClient(spec=SPEC, use_cache=False)
        original = ChatClient._request
        ChatClient._request = scripted(answer)
        self.addCleanup(setattr, ChatClient, "_request", original)

        version = select_versions(self.repo, "master", max_versions=1)[0]
        import pko.agent.loop as loop_module

        real_run = loop_module.run_scout
        loop_module.run_scout = lambda **kw: real_run(**{**kw, "client": client})
        self.addCleanup(setattr, loop_module, "run_scout", real_run)

        import pko.pipeline as pipeline_module

        pipeline_module.run_scout = loop_module.run_scout
        self.addCleanup(setattr, pipeline_module, "run_scout", real_run)

        return analyze_version(
            repo=self.repo, version=version, repo_name="mini_repo",
            branch="master", scout=SPEC,
        )

    def test_agent_findings_reach_the_model(self):
        answer = json.dumps({"final": {
            "facts": [{"kind": "GRAPH_NODE", "claim": "узел run_sql графа",
                       "path": "backend/src/agent/graph.py", "line": 9}],
            "groups": [{"name": "Оркестрация графа", "paths": ["backend/src/agent"]}],
            "process_trajectory": ["план", "поиск схем", "исполнение SQL"],
            "guardrail_invariants": [],
        }}, ensure_ascii=False)

        analysis = self._analyze(answer)
        self.assertIsNotNone(analysis.agent)

        names = [b.name for b in analysis.model.by_kind("BBB")]
        self.assertIn("Оркестрация графа", names,
                      msg="группировка агента должна заменять группировку по пакетам")
        self.assertEqual(analysis.model.meta.get("grouping_source"), "agent")
        self.assertEqual(analysis.model.meta.get("assembler"),
                         "не используется: разведку ведёт агент")

    def test_empty_groups_fall_back_to_packages(self):
        analysis = self._analyze(json.dumps({"final": {"facts": [], "groups": []}}))
        self.assertEqual(analysis.model.meta.get("grouping_source"), "packages")
        self.assertTrue(
            any("не предложил ни одной годной группы" in g for g in analysis.model.gaps)
        )

    def test_verdict_is_still_computed_by_code(self):
        """Что бы агент ни предложил, вердикт остаётся детерминированным."""
        analysis = self._analyze(json.dumps({"final": {"facts": [], "groups": []}}))
        self.assertIn(analysis.decision.decision,
                      {"ALLOW", "ALLOW_WITH_RESTRICTIONS", "DENY",
                       "REQUIRE_FULL_CONTOUR", "NO_DECISION"})
        self.assertIsNotNone(analysis.checks)


class PromptTest(unittest.TestCase):
    PROMPTS = Path(__file__).parent.parent / "src/pko/agent/prompts"

    def test_prompt_is_packaged_next_to_code(self):
        text, version, sha = load_prompt()
        self.assertTrue(self.PROMPTS.joinpath("scout_core.md").exists())
        self.assertIn("final", text)
        self.assertNotEqual(version, "0")
        self.assertEqual(len(sha), 12)

    def test_every_pack_is_loadable_and_named_in_the_detector(self):
        """Пак без соответствия в детекторе никогда не подключится — это мёртвый файл."""
        from pko.agent.stack import _PACK_BY_DEP, _PACK_BY_EXT, _PACK_BY_MECHANISM

        known = set(_PACK_BY_DEP) | set(_PACK_BY_EXT) | set(_PACK_BY_MECHANISM.values())
        on_disk = {p.stem for p in self.PROMPTS.joinpath("packs").glob("*.md")}
        self.assertTrue(on_disk, msg="паки должны лежать рядом с ядром")
        self.assertEqual(on_disk - known, set(), msg="пак, который нечем включить")

        composed, _, sha = load_prompt(packs=sorted(on_disk))
        core, _, core_sha = load_prompt()
        self.assertGreater(len(composed), len(core))
        self.assertNotEqual(sha, core_sha)

    def test_core_prompt_names_no_framework(self):
        """Ядро обязано остаться нейтральным: примеры фреймворков живут в паках."""
        core, _, _ = load_prompt()
        for word in ("FastAPI", "LangGraph", "SQLAlchemy", "Django"):
            with self.subTest(word=word):
                self.assertNotIn(word, core)


if __name__ == "__main__":
    unittest.main()


class BbbOwnershipTest(AgentTestCase):
    """Владение атомарной операцией не должно зависеть от имён групп."""

    def _model(self, group_name: str):
        from pko.assemble.heuristic import build_model

        candidates = build_candidates(self.extraction)
        # Часть кандидатов каталога уходит в группу агента, часть остаётся:
        # тогда один пакет числится сразу за двумя блоками, и владельца
        # атомарной операции нельзя выбирать по порядку блоков.
        agent_pkg = [c for c in candidates if c.group == "backend/src/agent"]
        db_pkg = [c for c in candidates if c.group == "backend/src/db_tools"]
        self.assertGreater(len(agent_pkg), 2, msg="фикстура должна давать делимый пакет")
        self.assertTrue(db_pkg, msg="нужен кандидат из второго каталога")
        groups = {group_name: [c.id for c in agent_pkg[:2] + db_pkg]}
        return build_model(
            extraction=self.extraction, candidates=candidates,
            meta={"repo": "mini_repo", "commit": self.sha, "version_label": "current"},
            intent=None, bbb_groups=groups or None,
        )

    def test_operation_identity_survives_group_rename(self):
        first = self._model("Аисполнение SQL")
        second = self._model("Яисполнение SQL")

        def keys(model):
            return {
                (o.name, (o.links.get("stable_key") or [""])[0])
                for o in model.objects if o.kind == "AO"
            }

        self.assertEqual(keys(first), keys(second),
                         msg="переименование группы сдвинуло идентичность операций")

    def test_every_sql_operation_has_an_owner(self):
        model = self._model("Исполнение SQL")
        orphans = [o.name for o in model.objects
                   if o.kind == "AO" and not (o.links.get("bbb") or [""])[0]]
        self.assertEqual(orphans, [], msg="операции без блока теряются в отчёте")


class MultistackTest(unittest.TestCase):
    """Стек без FastAPI, LangGraph и SQL: универсальные наблюдения вместо молчания.

    Раньше находке из этого репозитория не было места в модели: обработчик
    события, команда CLI и отправка в очередь не подходили ни под один вид, и
    агент либо молчал, либо подгонял их под чужой `ROUTE`.
    """

    @classmethod
    def setUpClass(cls):
        from fixture_support import ensure_multistack_fixture

        cls.repo = GitRepo(ensure_multistack_fixture())
        cls.sha = cls.repo.resolve("master")
        cls.tree = Tree.at(cls.repo, cls.sha)
        cls.extraction = extract_all(cls.tree)

    def run_agent(self, *answers):
        client = ChatClient(spec=SPEC, use_cache=False)
        original = ChatClient._request
        ChatClient._request = scripted(*answers)
        self.addCleanup(setattr, ChatClient, "_request", original)
        return run_scout(
            tree=self.tree, extraction=self.extraction, spec=SPEC,
            meta={"repo": "multistack", "commit": self.sha, "version_label": "current"},
            client=client,
        )

    def test_stack_without_python_frameworks_selects_its_packs(self):
        from pko.agent.stack import MANUAL_ONLY_PACKS, detect

        profile = detect(self.tree, self.extraction)
        for pack in ("jobs", "messaging"):
            with self.subTest(pack=pack):
                self.assertIn(pack, profile.packs)
        self.assertNotIn("agents", profile.packs, msg="графа исполнения здесь нет")
        # Периметр первой версии — backend. Пак `frontend` признан по коду
        # (причина сохранена), но сам не подключается: находки по `.tsx`
        # статическому разбору сверить не с чем.
        self.assertIn("frontend", MANUAL_ONLY_PACKS)
        self.assertNotIn("frontend", profile.packs)
        self.assertIn("frontend", profile.reasons,
                      msg="причина всё равно должна быть видна оператору")

    def test_frontend_outside_perimeter_is_not_repeated_as_agent_gap(self):
        result = self.run_agent(json.dumps({"final": {}}))
        note = " ".join(result.notes)
        self.assertNotIn(".jsx", note)
        self.assertNotIn("только агентом", note,
                         msg="границу frontend уже один раз называет extractor")

    def test_ui_cli_and_queue_observations_are_accepted(self):
        """Три механизма, которых не знает статический разбор, попадают в модель."""
        observations = [
            {"category": "ENTRYPOINT", "action": "serve", "mechanism": "ui_event",
             "claim": "кнопка submitOrder оформляет заказ",
             "path": "ui/src/OrderForm.jsx", "line": 13},
            {"category": "ENTRYPOINT", "mechanism": "cli",
             "claim": "команда batch объявлена add_argument",
             "path": "cli/main.py", "line": 11},
            {"category": "EFFECT", "action": "emit", "mechanism": "queue",
             "claim": "отправка orders.processed в брокер",
             "path": "workers/consumer.py", "line": 11},
            {"category": "EFFECT", "action": "write", "mechanism": "fs",
             "claim": "выгрузка строк в файл handle.write",
             "path": "store/files.py", "line": 12},
        ]
        result = self.run_agent(json.dumps({"final": {"facts": observations}}))

        self.assertEqual(result.trace.rejected_facts, [],
                         msg=f"отброшено: {result.trace.rejected_facts}")
        mechanisms = {f.facets.mechanism for f in result.facts}
        self.assertEqual(mechanisms, {"ui_event", "cli", "queue", "fs"})
        by_mechanism = {f.facets.mechanism: f.gate_eligible for f in result.facts}
        self.assertTrue(by_mechanism["ui_event"])
        self.assertTrue(by_mechanism["cli"])
        self.assertTrue(by_mechanism["queue"])
        self.assertFalse(by_mechanism["fs"],
                         msg="файловые эффекты видны в отчёте, но не входят в Gate")

    def test_observation_in_unknown_mechanism_stays_out_of_the_verdict(self):
        """Незнакомый механизм не отбрасывается, но и вердикт им подкрепить нельзя."""
        result = self.run_agent(json.dumps({"final": {"facts": [{
            "category": "EFFECT", "action": "call", "mechanism": "smart_contract",
            "claim": "вызов export отправляет данные в реестр",
            "path": "store/files.py", "line": 12,
        }]}}))
        self.assertEqual(len(result.facts), 1, msg="находка обязана остаться в отчёте")
        self.assertFalse(result.facts[0].gate_eligible)
        self.assertIn("не входит", result.trace.accepted_facts[0]["reason"])

    def test_claim_without_construction_is_still_rejected(self):
        """Универсализация не ослабила проверку: слова в коде недостаточно."""
        result = self.run_agent(json.dumps({"final": {"facts": [{
            "category": "EFFECT", "action": "write", "mechanism": "sql",
            "claim": "функция export пишет строки в базу",
            "path": "store/files.py", "line": 12,
        }]}}))
        self.assertEqual(result.facts, [])
        self.assertIn("нет соответствующей конструкции",
                      result.trace.rejected_facts[0]["reason"])

    def test_observations_become_blocks_and_operations(self):
        """Главный результат универсализации: на чужом стеке модель перестаёт быть пустой.

        Без агента этот репозиторий даёт ноль блоков и ноль операций —
        статический разбор не знает ни JSX, ни argparse, ни брокера.
        """
        from fixture_support import ensure_multistack_fixture
        from pko.history.selector import select_versions
        from pko.pipeline import analyze_version

        answer = json.dumps({"final": {
            "facts": [
                {"category": "ENTRYPOINT", "action": "serve", "mechanism": "ui_event",
                 "claim": "кнопка submitOrder оформляет заказ",
                 "path": "ui/src/OrderForm.jsx", "line": 13},
                {"category": "ENTRYPOINT", "mechanism": "cli",
                 "claim": "команда batch объявлена add_argument",
                 "path": "cli/main.py", "line": 11},
                {"category": "EFFECT", "action": "emit", "mechanism": "queue",
                 "claim": "отправка orders.processed в брокер",
                 "path": "workers/consumer.py", "line": 11},
                {"category": "EFFECT", "action": "write", "mechanism": "fs",
                 "claim": "выгрузка строк в файл handle.write",
                 "path": "store/files.py", "line": 12},
            ],
            "groups": [{"name": "Оформление заказа", "paths": ["ui/src"]},
                       {"name": "Пакетная обработка", "paths": ["cli", "workers"]}],
        }})
        original = ChatClient._request
        ChatClient._request = scripted(answer)
        self.addCleanup(setattr, ChatClient, "_request", original)

        repo = GitRepo(ensure_multistack_fixture())
        version = select_versions(repo, "master", max_versions=1)[-1]
        analysis = analyze_version(repo=repo, version=version, repo_name="multistack",
                                   branch="master", scout=SPEC)

        names = {o.name for o in analysis.model.objects if o.kind == "BBB"}
        self.assertIn("Оформление заказа", names)
        self.assertIn("Пакетная обработка", names)

        operations = {o.name for o in analysis.model.objects if o.kind == "AO"}
        self.assertEqual(operations, {"Запись файла", "Отправка сообщения в очередь"},
                         msg="эффекты вне SQL обязаны становиться атомарными операциями")

        trajectory = next(c for c in analysis.checks if c.id == "CHK-AP-001")
        self.assertIn("найдены точки входа", trajectory.basis,
                      msg="UI и CLI теперь считаются точками входа")

    def test_write_declared_as_entrypoint_is_rejected(self):
        """Механизм подтверждён конструкцией — категорию нельзя принять на слово.

        Иначе настоящая запись, названная точкой входа, исчезала бы из
        эффектов и попадала в доказательства восстановленной траектории:
        ошибка классификации меняла бы вход детерминированного Gate.
        """
        result = self.run_agent(json.dumps({"final": {"facts": [{
            "category": "ENTRYPOINT", "action": "write", "mechanism": "fs",
            "claim": "выгрузка строк handle.write",
            "path": "store/files.py", "line": 12,
        }]}}))
        self.assertEqual(result.facts, [])
        self.assertIn("признаки наблюдения противоречивы",
                      result.trace.rejected_facts[0]["reason"])

    def test_legacy_kind_cannot_be_relabelled(self):
        """`SQL_WRITE` уже означает EFFECT/write/sql — переобъявить смысл нельзя."""
        result = self.run_agent(json.dumps({"final": {"facts": [{
            "kind": "SQL_WRITE", "category": "ENTRYPOINT",
            "claim": "выгрузка строк handle.write",
            "path": "store/files.py", "line": 12,
        }]}}))
        self.assertEqual(result.facts, [])
        self.assertIn("вид SQL_WRITE означает", result.trace.rejected_facts[0]["reason"])

    def test_queue_may_be_either_effect_or_entrypoint(self):
        """Ограничение не должно запрещать законную двойственность механизма."""
        result = self.run_agent(json.dumps({"final": {"facts": [
            {"category": "EFFECT", "action": "emit", "mechanism": "queue",
             "claim": "отправка orders.processed в брокер",
             "path": "workers/consumer.py", "line": 11},
            {"category": "ENTRYPOINT", "action": "serve", "mechanism": "queue",
             "claim": "обработчик consume слушает orders.new",
             "path": "workers/consumer.py", "line": 14},
        ]}}))
        self.assertEqual(result.trace.rejected_facts, [],
                         msg=f"отброшено: {result.trace.rejected_facts}")
        self.assertEqual({f.facets.category for f in result.facts},
                         {"EFFECT", "ENTRYPOINT"})

    def test_entrypoint_in_unsupported_mechanism_does_not_feed_the_gate(self):
        """Категория сама по себе проверку не наполняет: нужен механизм с проверкой."""
        from pko.extractors.base import Fact
        from pko.extractors.runner import Extraction
        from pko.gate import policies

        exotic = Fact(kind="ENTRYPOINT", key="вход", value="x", path="a.py", line=1,
                      category="ENTRYPOINT", mechanism="smart_contract")
        supported = Fact(kind="ROUTE", key="POST /t", value="x", path="b.py", line=1)
        found = policies.entrypoints(Extraction(facts=[exotic, supported]))
        self.assertEqual([f.path for f in found], ["b.py"])

    def test_unknown_category_is_accepted_as_the_prompt_asks(self):
        """Промпт просит `UNKNOWN` вместо выдуманной точки входа — значит, он должен приниматься."""
        result = self.run_agent(json.dumps({"final": {"facts": [{
            "category": "UNKNOWN",
            "claim": "назначение listen из кода не следует",
            "path": "workers/consumer.py", "line": 13,
        }]}}))
        self.assertEqual(result.trace.rejected_facts, [],
                         msg=f"отброшено: {result.trace.rejected_facts}")
        self.assertEqual(len(result.facts), 1)
        self.assertFalse(result.facts[0].gate_eligible,
                         msg="толкования нет — вердикта такое наблюдение не касается")

    def test_same_finding_in_two_spellings_is_counted_once(self):
        """`note_fact` нормализует признаки, финал — нет: ключ обязан приводить обе стороны."""
        note = tool_call("note_fact", category="EFFECT", action="write", mechanism="fs",
                         claim="выгрузка строк handle.write",
                         path="store/files.py", line=12)
        final = json.dumps({"final": {"facts": [{
            "category": "EFFECT", "action": "Write", "mechanism": "FS",
            "claim": "выгрузка строк handle.write",
            "path": "store/files.py", "line": 12,
        }]}})
        result = self.run_agent(note, final)
        self.assertEqual(len(result.facts), 1,
                         msg="одна находка в двух написаниях удваивала «Количество мест вызова»")

    def test_trace_names_universal_observations(self):
        """Трасса — главный продукт режима: строка без типа в ней бесполезна."""
        result = self.run_agent(json.dumps({"final": {"facts": [{
            "category": "EFFECT", "action": "write", "mechanism": "fs",
            "claim": "выгрузка строк handle.write",
            "path": "store/files.py", "line": 12,
        }]}}))
        html = render_trace(result.trace)
        self.assertIn("EFFECT/write/fs", html)


class PackValidationTest(unittest.TestCase):
    def test_unknown_pack_name_is_refused(self):
        """Молчаливый пропуск записал бы в трассу условия, которых не было."""
        from pko.agent.loop import load_prompt
        from pko.errors import PkoError

        with self.assertRaises(PkoError) as ctx:
            load_prompt(packs=["opu"])
        self.assertIn("opu", ctx.exception.message)
        self.assertIn("доступны", ctx.exception.hint)

    def test_cli_rejects_unknown_pack(self):
        from types import SimpleNamespace

        from pko.cli import _packs
        from pko.errors import PkoError

        self.assertIsNone(_packs(SimpleNamespace(agent_packs=None)))
        self.assertEqual(_packs(SimpleNamespace(agent_packs="web, data")), ["web", "data"])
        with self.assertRaises(PkoError):
            _packs(SimpleNamespace(agent_packs="web,нет-такого"))


class IntakeValidationTest(MultistackTest):
    """Оба пути приёма наблюдений судят по одному правилу.

    Механизм обязателен не из формализма: без него структурной проверки не
    существует вовсе, а наблюдение попадало в поля паспорта с происхождением
    `OBSERVED` — «код это показывает» — имея за собой только совпадение слова
    из формулировки.
    """

    def _both_paths(self, **facets):
        """Вернуть (принято финалом, принято инструментом) для одного и того же входа."""
        payload = {"claim": "выгрузка строк handle.write",
                   "path": "store/files.py", "line": 12, **facets}
        result = self.run_agent(json.dumps({"final": {"facts": [payload]}}))
        tool = ToolBox(tree=self.tree).note_fact(
            claim=payload["claim"], path=payload["path"], line=payload["line"],
            kind=payload.get("kind", ""), category=payload.get("category", ""),
            action=payload.get("action", ""), mechanism=payload.get("mechanism", ""),
        )
        return result, tool

    def test_category_without_mechanism_is_refused_on_both_paths(self):
        result, tool = self._both_paths(category="ENTRYPOINT")
        self.assertEqual(result.facts, [])
        self.assertIn("без mechanism", result.trace.rejected_facts[0]["reason"])
        self.assertFalse(tool.ok)
        self.assertIn("без mechanism", tool.content)

    def test_category_in_kind_without_mechanism_is_refused_on_both_paths(self):
        """`kind` не должен быть обходом правил универсальной category."""
        result, tool = self._both_paths(kind="ENTRYPOINT")
        self.assertEqual(result.facts, [])
        self.assertIn("без mechanism", result.trace.rejected_facts[0]["reason"])
        self.assertFalse(tool.ok)
        self.assertIn("без mechanism", tool.content)

    def test_category_in_kind_with_mechanism_is_accepted_on_both_paths(self):
        from pko.model import taxonomy

        result, tool = self._both_paths(kind="EFFECT", action="write", mechanism="fs")
        self.assertEqual(len(result.facts), 1)
        self.assertEqual(result.facts[0].facets, taxonomy.Facets("EFFECT", "write", "fs"))
        self.assertTrue(tool.ok)

    def test_category_in_kind_cannot_be_overridden(self):
        result, tool = self._both_paths(
            kind="ENTRYPOINT", category="EFFECT", action="write", mechanism="fs",
        )
        self.assertEqual(result.facts, [])
        self.assertIn("означает category=ENTRYPOINT",
                      result.trace.rejected_facts[0]["reason"])
        self.assertFalse(tool.ok)

    def test_misspelled_action_is_refused_not_erased(self):
        """Стёртое действие превращало `EFFECT/write/fs` в бесцветное `EFFECT//fs`."""
        result, tool = self._both_paths(category="EFFECT", action="wrote", mechanism="fs")
        self.assertEqual(result.facts, [])
        self.assertIn("неизвестное action", result.trace.rejected_facts[0]["reason"])
        self.assertFalse(tool.ok)

    def test_misspelled_category_is_named_as_such(self):
        result, tool = self._both_paths(category="ENTRYPOI", mechanism="cli")
        self.assertEqual(result.facts, [])
        self.assertIn("неизвестная category", result.trace.rejected_facts[0]["reason"])
        self.assertFalse(tool.ok)

    def test_unknown_category_still_needs_no_mechanism(self):
        """«Толкования нет» — законное наблюдение, механизма у него быть не может."""
        result, tool = self._both_paths(category="UNKNOWN")
        self.assertEqual(len(result.facts), 1)
        self.assertFalse(result.facts[0].gate_eligible)
        self.assertTrue(tool.ok)

    def test_unverified_entrypoint_cannot_reach_observed_fields(self):
        """Главное следствие: в «Условия запуска» больше не попадает недоказанное."""
        from pko.assemble.heuristic import build_model

        result = self.run_agent(json.dumps({"final": {"facts": [{
            "category": "ENTRYPOINT",
            "claim": "кнопка submitOrder оформляет заказ",
            "path": "ui/src/OrderForm.jsx", "line": 13,
        }]}}))
        self.assertEqual(result.facts, [])

        extraction = extract_all(self.tree)
        extraction.facts.extend(result.facts)
        model = build_model(extraction=extraction,
                            candidates=build_candidates(extraction),
                            meta={"commit": self.sha, "repo": "multistack"})
        process = next(o for o in model.objects if o.kind == "PROCESS")
        self.assertEqual(process.fields["Условия запуска"].origin, "UNKNOWN",
                         msg="точка входа без механизма не должна выглядеть наблюдаемой")

    def test_tool_reply_matches_what_the_verdict_will_do(self):
        """Инструмент не должен обещать больше, чем даст проверка.

        У SQL шаблон есть, но в вердикт наблюдение агента не идёт: запрос
        живёт в строковом литерале, и regex не отличит исполняемый от примера
        в документации. Ответ `note_fact` обязан говорить это сразу, а не
        оставлять узнавание на чтение отчёта.
        """
        box = ToolBox(tree=self.tree)
        sql = box.note_fact(claim="чтение hr_headcount запросом SELECT",
                            path="store/files.py", line=3,
                            category="EFFECT", action="read", mechanism="sql")
        self.assertTrue(sql.ok)
        self.assertIn("в вердикт Gate", sql.content)

        cli = box.note_fact(claim="команда batch объявлена add_argument",
                            path="cli/main.py", line=11,
                            category="ENTRYPOINT", mechanism="cli")
        self.assertTrue(cli.ok)
        self.assertNotIn("в вердикт Gate", cli.content)
