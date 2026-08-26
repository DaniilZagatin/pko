"""Инструменты единого агента (`read_slides`/`list_files`/`read_file`/`search`) —
read-only над презентацией и деревом коммита, на реальной фикстуре `mini_repo`.
"""

import unittest

from fixture_support import ensure_fixture
from pko.extractors.base import Tree
from pko.git.repo import GitRepo
from pko.progress.agent_tools import (
    TOOL_SCHEMAS,
    ToolBox,
    _glob_filter,
    _is_blocked,
    _mask_secrets,
    _paginate,
)
from pko.progress.pptx_reader import Slide, SlideShape

ROUTER_PATH = "backend/src/api/v1/router.py"

SLIDES = [
    Slide(number=1, heading="Задачи спринта", shapes=[
        SlideShape(text="Постановка задач в обработку", left=0.5, top=1.2, width=4.0, height=2.2),
    ]),
    Slide(number=2, heading="Таймлайн", shapes=[
        SlideShape(text="Этап 1: MVP", left=0.5, top=2.5, width=2.9, height=1.6),
    ]),
]


class ToolSchemasTest(unittest.TestCase):
    def test_schemas_match_toolbox_dispatch(self):
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        self.assertEqual(names, {"read_slides", "list_files", "read_file", "search"})
        for schema in TOOL_SCHEMAS:
            self.assertEqual(schema["type"], "function")
            self.assertIn("properties", schema["function"]["parameters"])


class ToolBoxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        repo = GitRepo(ensure_fixture())
        cls.tree = Tree.at(repo, repo.resolve("master"))

    def setUp(self):
        self.tools = ToolBox(self.tree, slides=SLIDES)

    def test_read_slides_renders_known_text(self):
        result = self.tools.read_slides()
        self.assertTrue(result.ok)
        self.assertIn("Постановка задач в обработку", result.content)
        self.assertIn("Этап 1: MVP", result.content)

    def test_read_slides_respects_pagination(self):
        result = self.tools.read_slides(offset=1, limit=1)
        self.assertTrue(result.ok)
        self.assertIn("Задачи спринта", result.content)
        self.assertNotIn("Таймлайн", result.content)
        self.assertIn("есть ещё слайды", result.content)

    def test_read_slides_without_slides_is_not_an_error(self):
        empty = ToolBox(self.tree)
        result = empty.read_slides()
        self.assertTrue(result.ok)
        self.assertIn("слайдов нет", result.content)

    def test_list_files_finds_known_path(self):
        result = self.tools.list_files()
        self.assertTrue(result.ok)
        self.assertIn(ROUTER_PATH, result.content)

    def test_list_files_respects_glob(self):
        result = self.tools.list_files(glob="*.py")
        self.assertTrue(result.ok)
        for line in result.content.splitlines():
            if line.startswith("..."):
                continue
            self.assertTrue(line.endswith(".py"), msg=line)

    def test_read_file_returns_numbered_lines(self):
        result = self.tools.read_file(ROUTER_PATH)
        self.assertTrue(result.ok)
        self.assertIn("1: from fastapi import APIRouter", result.content)

    def test_read_file_offset_and_limit(self):
        windowed = self.tools.read_file(ROUTER_PATH, offset=2, limit=1)
        self.assertTrue(windowed.ok)
        first_line = windowed.content.splitlines()[0]
        self.assertTrue(first_line.startswith("2:"), msg=first_line)
        # Роутер длиннее одной строки — должна появиться пометка о продолжении.
        self.assertIn("есть ещё строки", windowed.content)

    def test_read_file_missing_path_is_a_clean_failure(self):
        result = self.tools.read_file("backend/src/does_not_exist.py")
        self.assertFalse(result.ok)
        self.assertIn("не найден", result.content)

    def test_read_file_blocks_env_and_key_files(self):
        for path in (".env", ".env.local", "secrets.pem", "id.key"):
            self.assertTrue(_is_blocked(path), msg=path)
        self.assertFalse(_is_blocked(ROUTER_PATH))

    def test_search_finds_known_symbol(self):
        result = self.tools.search(pattern="APIRouter")
        self.assertTrue(result.ok)
        self.assertIn(f"{ROUTER_PATH}:", result.content)

    def test_search_no_matches_is_not_an_error(self):
        result = self.tools.search(pattern="totally_absent_symbol_xyz")
        self.assertTrue(result.ok)
        self.assertIn("совпадений нет", result.content)

    def test_search_rejects_invalid_regex(self):
        result = self.tools.search(pattern="(unclosed")
        self.assertFalse(result.ok)
        self.assertIn("regex", result.content)

    def test_search_rejects_catastrophic_shape(self):
        result = self.tools.search(pattern=r"(a+)+$")
        self.assertFalse(result.ok)
        self.assertIn("backtracking", result.content)

    def test_unknown_tool_is_a_clean_failure(self):
        result = self.tools.call("delete_everything", {})
        self.assertFalse(result.ok)
        self.assertIn("неизвестный инструмент", result.content)


class MaskSecretsTest(unittest.TestCase):
    def test_key_value_assignment_is_masked(self):
        masked = _mask_secrets('api_key = "sk-abc123def456"')
        self.assertNotIn("sk-abc123def456", masked)
        self.assertIn("***", masked)

    def test_ordinary_code_is_untouched(self):
        line = "def read_file(path, offset=1):"
        self.assertEqual(_mask_secrets(line), line)


class PaginateAndGlobTest(unittest.TestCase):
    def test_paginate_reports_has_more(self):
        items = [str(i) for i in range(10)]
        page, has_more = _paginate(items, offset=1, limit=4)
        self.assertEqual(page, ["0", "1", "2", "3"])
        self.assertTrue(has_more)
        page2, has_more2 = _paginate(items, offset=9, limit=4)
        self.assertEqual(page2, ["8", "9"])
        self.assertFalse(has_more2)

    def test_glob_filter_matches_suffix_pattern(self):
        paths = ["a/b.py", "a/b.md", "c/d.py"]
        self.assertEqual(_glob_filter(paths, "*.py"), ["a/b.py", "c/d.py"])
        self.assertEqual(_glob_filter(paths, "*"), paths)


if __name__ == "__main__":
    unittest.main()
