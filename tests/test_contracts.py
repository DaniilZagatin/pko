"""Объявленные контракты и политики как источник фактов.

Главное здесь — не полнота разбора, а граница: объявление не должно давать
допуск. Репозиторий с одной спецификацией и без кода обязан провалить проверку
«траектория восстанавливается из реализации».
"""

import json
import unittest

from pko.extractors import contracts, policy_specs
from pko.extractors.base import Tree
from pko.extractors.runner import ANALYZED_GLOBS, extract_all

OPENAPI = """
openapi: 3.0.0
info:
  title: Orders API
paths:
  /orders:
    get:
      summary: Список заказов
    post:
      summary: Создать заказ
  /health:
    get:
      summary: Проверка живости
"""

SCHEMA = json.dumps({
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "OrderEvent",
    "type": "object",
    "required": ["order_id", "amount"],
    "properties": {"order_id": {"type": "string"}, "amount": {"type": "number"},
                   "note": {"type": "string"}},
}, ensure_ascii=False)

TOOLS = json.dumps({
    "tools": [
        {"function": {"name": "search_docs", "description": "Поиск по документам"}},
        {"name": "send_email", "description": "Отправка письма"},
        "plain_tool",
    ]
}, ensure_ascii=False)

FUNCTIONS_WITH_SCHEMA = json.dumps({
    "$schema": "https://internal.example/schemas/tool-manifest-v1.json",
    "functions": [
        {"name": "lookup_customer", "description": "Найти клиента"},
        {"name": "prepare_answer", "description": "Подготовить ответ"},
    ],
}, ensure_ascii=False)

POLICY = """
agent:
  timeout: 30
  max_retries: 3
  mode: CONFIRM
  allowed_hosts:
    - internal.example
    - api.example
limits:
  max_tokens: 4096
"""

EVAL_SPEC = """
name: acceptance
cases:
  - id: c1
  - id: c2
  - id: c3
"""


class FakeTree:
    """Минимальное дерево: экстракторам нужны только имена и содержимое."""

    def __init__(self, files: dict[str, str]):
        self._files = files

    @property
    def files(self):
        return list(self._files)

    def read(self, path):
        return self._files.get(path)


class MemoryRepo:
    """Репозиторий в памяти для сквозной проверки runner без git-фикстуры."""

    def __init__(self, files: dict[str, str]):
        self.files = files

    def read_text(self, sha, path):
        return self.files.get(path)


def extract_files(files: dict[str, str]):
    repo = MemoryRepo(files)
    tree = Tree(repo=repo, sha="coverage-test", files=list(files))
    return extract_all(tree)


class OpenApiTest(unittest.TestCase):
    def setUp(self):
        self.facts = contracts.extract(FakeTree({"docs/openapi.yaml": OPENAPI}))

    def test_declared_routes_become_entrypoints(self):
        keys = {f.key for f in self.facts}
        self.assertEqual(keys, {"GET /orders", "POST /orders", "GET /health"})

    def test_entrypoint_facets_are_consistent(self):
        for fact in self.facts:
            with self.subTest(key=fact.key):
                self.assertEqual(fact.facets.category, "ENTRYPOINT")
                self.assertEqual(fact.facets.mechanism, "http_server")

    def test_declaration_cannot_grant_admission(self):
        """Спецификация — не реализация; допуск на неё не выдаётся."""
        self.assertTrue(all(not f.gate_eligible for f in self.facts))

    def test_basis_says_the_route_is_declared_not_found(self):
        for fact in self.facts:
            with self.subTest(key=fact.key):
                self.assertTrue(fact.basis.startswith("объявлен"))

    def test_a_file_named_openapi_without_the_structure_is_ignored(self):
        """Опознаём по структуре: имя файла ничего не доказывает."""
        facts = contracts.extract(FakeTree({"openapi.yaml": "title: просто заметки\n"}))
        self.assertEqual(facts, [])


class SchemaTest(unittest.TestCase):
    def test_schema_is_an_artifact_not_a_state_transition(self):
        facts = contracts.extract(FakeTree({"events/order.json": SCHEMA}))
        self.assertEqual(len(facts), 1)
        fact = facts[0]
        self.assertEqual(fact.facets.category, "ARTIFACT")
        self.assertEqual(fact.key, "schema:OrderEvent")
        self.assertIn("полей 3", fact.basis)
        self.assertIn("обязательных 2", fact.basis)


class ToolManifestTest(unittest.TestCase):
    def setUp(self):
        self.facts = contracts.extract(FakeTree({"agent/tools.json": TOOLS}))

    def test_all_manifest_shapes_are_read(self):
        self.assertEqual({f.key for f in self.facts},
                         {"search_docs", "send_email", "plain_tool"})

    def test_tools_are_steps_of_the_trajectory(self):
        for fact in self.facts:
            with self.subTest(key=fact.key):
                self.assertEqual(fact.facets.category, "STEP")
                self.assertEqual(fact.facets.mechanism, "agent_tool")
                self.assertFalse(fact.gate_eligible)

    def test_schema_validated_functions_manifest_keeps_its_tools(self):
        """Top-level `$schema` описывает manifest и не превращает его в пустую JSON Schema."""
        facts = contracts.extract(FakeTree({
            "agent/functions.json": FUNCTIONS_WITH_SCHEMA,
        }))

        self.assertEqual({f.key for f in facts}, {"lookup_customer", "prepare_answer"})
        self.assertTrue(all(f.kind == "TOOL" for f in facts))

    def test_recognizers_are_composable_when_document_has_two_roles(self):
        payload = json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "title": "ToolSet",
            "properties": {"query": {"type": "string"}},
            "tools": ["search_docs"],
        })
        facts = contracts.extract(FakeTree({"agent/tools.schema.json": payload}))

        self.assertIn("schema:ToolSet", {f.key for f in facts})
        self.assertIn("search_docs", {f.key for f in facts})


class PolicySpecTest(unittest.TestCase):
    def setUp(self):
        self.facts = policy_specs.extract(FakeTree({"config/agent.yaml": POLICY}))
        self.by_key = {f.key: f for f in self.facts}

    def test_nested_limits_are_found(self):
        """`agent.limits.timeout` — тоже ограничение: плоский разбор его терял."""
        self.assertIn("agent.timeout", self.by_key)
        self.assertIn("limits.max_tokens", self.by_key)
        self.assertEqual(self.by_key["agent.timeout"].value, 30)

    def test_allowlist_is_a_control(self):
        fact = self.by_key["agent.allowed_hosts"]
        self.assertEqual(fact.facets.category, "CONTROL")
        self.assertEqual(fact.facets.mechanism, "allowlist")
        self.assertEqual(fact.value, ["internal.example", "api.example"])

    def test_mode_is_not_filed_as_a_limit(self):
        """Режим задаёт объём полномочий, а не величину ограничения."""
        fact = self.by_key["agent.mode"]
        self.assertEqual(fact.value, "CONFIRM")
        self.assertNotEqual(fact.facets.mechanism, "limit")
        self.assertIn("статически не проверяется", fact.basis)

    def test_declared_controls_do_not_feed_the_verdict(self):
        """Вынести таймаут в YAML — не то же самое, что применить его перед вызовом."""
        self.assertTrue(all(not f.gate_eligible for f in self.facts))

    def test_owner_intent_is_not_read_as_implementation_config(self):
        """`requested_mode` владельца — не объявление режима в коде системы."""
        facts = policy_specs.extract(FakeTree({"business_intent.yaml": "requested_mode: AUTO\n"}))
        self.assertEqual(facts, [])

    def test_booleans_are_not_mistaken_for_limits(self):
        facts = policy_specs.extract(FakeTree({"c.yaml": "timeout: true\n"}))
        self.assertEqual(facts, [])


class EvalSpecTest(unittest.TestCase):
    def test_specification_is_not_a_result(self):
        facts = policy_specs.extract(FakeTree({"evals/acceptance.yaml": EVAL_SPEC}))
        spec = next(f for f in facts if f.key.startswith("eval:"))
        self.assertEqual(spec.value, 3)
        self.assertIn("подтверждается только отчётом", spec.basis)
        self.assertFalse(spec.gate_eligible)

    def test_unreadable_file_is_not_a_run_failure(self):
        facts = policy_specs.extract(FakeTree({"c.yaml": "\t: [неразбираемое\n"}))
        self.assertEqual(facts, [])


class StructuredJsonCoverageTest(unittest.TestCase):
    """Coverage учитывает распознанный JSON, а не любое совпадение расширения."""

    def test_non_package_json_schema_is_counted_as_analyzed(self):
        extraction = extract_files({"events/order.schema.json": SCHEMA})

        self.assertEqual(extraction.coverage.files_total, 1)
        self.assertEqual(extraction.coverage.files_analyzed, 1)
        self.assertEqual(extraction.coverage.ratio, 1.0)
        self.assertIn("*.json", extraction.coverage.analyzed_globs)
        self.assertIn("*.json", ANALYZED_GLOBS)

    def test_unrecognized_and_malformed_json_are_not_credited(self):
        for path, content in (
            ("data/arbitrary.json", json.dumps({"rows": [1, 2, 3]})),
            ("data/broken.json", '{"rows": [1, 2,}'),
        ):
            with self.subTest(path=path):
                extraction = extract_files({path: content})
                self.assertEqual(extraction.coverage.files_total, 1)
                self.assertEqual(extraction.coverage.files_analyzed, 0)
                self.assertEqual(extraction.coverage.skipped_globs, ["*.json"])


if __name__ == "__main__":
    unittest.main()
