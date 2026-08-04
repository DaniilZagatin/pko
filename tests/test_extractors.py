"""Разбор кода: границы эвристик, которые влияют на вердикт Gate."""

import ast
import tempfile
import unittest
from pathlib import Path

from pko.checks.test_link import confirming_cases
from pko.extractors.runner import Extraction
from pko.extractors.test_reports import load_junit
from pko.extractors.python_code import _walk_module


def facts_of(source: str, path: str = "backend/src/example.py"):
    return _walk_module(ast.parse(source), path)


class SqlDetectionTest(unittest.TestCase):
    """`SQL_WRITE` управляет проверкой «только чтение», а та блокирует запуск."""

    def kinds(self, source: str) -> set[str]:
        return {f.kind for f in facts_of(source)}

    def test_prose_about_dropping_is_not_sql(self):
        source = '''
def cleanup(path: str) -> None:
    """Drop the temporary upload after processing is complete."""
    return None
'''
        self.assertNotIn("SQL_WRITE", self.kinds(source))

    def test_other_bare_verbs_in_prose_are_not_sql(self):
        for phrase in (
            "Truncate the response to the first 100 characters.",
            "Alter the retry policy when the queue is full.",
            "Update the cached profile after each request.",
        ):
            with self.subTest(phrase=phrase):
                source = f'def helper() -> None:\n    """{phrase}"""\n    return None\n'
                self.assertNotIn("SQL_WRITE", self.kinds(source))

    def test_real_ddl_is_still_detected(self):
        for statement in (
            "DROP TABLE hr_tmp",
            "truncate table hr_stage",
            "ALTER TABLE hr_headcount ADD COLUMN dt date",
            "INSERT INTO hr_audit (id) VALUES (1)",
            "DELETE FROM hr_audit WHERE id = 1",
            "UPDATE hr_audit SET processed = true",
        ):
            with self.subTest(statement=statement):
                source = f'QUERY = "{statement} -- служебный запрос"\n'
                self.assertIn("SQL_WRITE", self.kinds(source))

    def test_select_is_a_read(self):
        source = 'QUERY = "SELECT employee_id FROM hr_headcount WHERE dt = :dt"\n'
        kinds = self.kinds(source)
        self.assertIn("SQL_READ", kinds)
        self.assertNotIn("SQL_WRITE", kinds)

    def test_prose_about_selecting_is_not_sql(self):
        source = '''
def choose(items):
    """Select the best candidate from the list."""
    return items[0]
'''
        self.assertNotIn("SQL_READ", self.kinds(source))


class SecretMaskingTest(unittest.TestCase):
    def test_secret_value_is_not_stored(self):
        source = 'llm_api_key = "очень-секретное-значение"\n'
        settings = [f for f in facts_of(source) if f.kind == "SETTING"]
        self.assertTrue(settings)
        self.assertEqual(settings[0].value, "<скрыто>")
        self.assertNotIn("очень-секретное", str(settings[0].value))


class LargeJUnitTest(unittest.TestCase):
    def test_enforcement_case_after_500_is_retained(self):
        with tempfile.TemporaryDirectory() as raw:
            report = Path(raw) / "junit.xml"
            cases = "".join(
                f'<testcase classname="tests.bulk" name="test_regular_{i}"/>'
                for i in range(500)
            )
            cases += (
                '<testcase classname="tests.guard" '
                'name="test_update_forbidden_read_only"/>'
            )
            report.write_text(
                f'<testsuite name="bulk" tests="501">{cases}</testsuite>',
                encoding="utf-8",
            )
            extraction = Extraction(facts=load_junit(report))
            self.assertIn(
                "test_update_forbidden_read_only",
                confirming_cases("read_only", extraction),
            )


if __name__ == "__main__":
    unittest.main()
