"""Сборка переносимого патча.

Патч уходит на другой компьютер, поэтому проверяется не «собрался ли файл», а
две вещи, которые дороже всего стоят при ошибке: не утёк ли секрет и не попало
ли в него то, что переносить нельзя.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "transfer"))

import make_bundle  # noqa: E402
import make_patch  # noqa: E402


class ExclusionTest(unittest.TestCase):
    def test_project_material_and_run_artifacts_are_excluded(self):
        for path in (
            "intent_ai_advisor.yaml",
            "business_requirements_ai_advisor.md",
            "pko-out/gate_card_current.md",
            # Каталоги прогонов операторы называют по-разному; точный
            # префикс `pko-out/` пропускал остальные, и в пакет уходили
            # отчёты с кодом анализируемой системы.
            "pko-out-llm2/passports_current.html",
            "pko-out-llm/semantic_facts.json",
            "bench/runs/20260101_GLM/pko-self/metrics.json",
            "tests/fixtures/mini_repo/backend/app.py",
            "src/pko/__pycache__/cli.cpython-313.pyc",
            "transfer/out/pko-x.patch",
            # Пакет прежней сборки в корне: без исключения он целиком уезжал
            # внутрь нового, и каждая сборка удваивала состав.
            "bundle/files/src/pko/cli.py",
            "bundle/SHA256SUMS",
            "bundle.zip",
            "transfer/bundle.zip",
            ".env.local",
        ):
            with self.subTest(path=path):
                self.assertTrue(make_patch.is_excluded(path))

    def test_source_and_tests_are_carried(self):
        for path in ("src/pko/cli.py", "tests/test_units.py", "README.md",
                     "transfer/make_patch.py", "examples/business_intent.yaml"):
            with self.subTest(path=path):
                self.assertFalse(make_patch.is_excluded(path))


class SecretScanTest(unittest.TestCase):
    """Патч не должен уносить ключ на другую машину."""

    def test_api_key_assignment_blocks_the_build(self):
        # Значение собирается из частей: строка вида `sk-…` в исходнике теста
        # заблокировала бы сборку патча этим же сканером — и была бы права.
        fake = "sk-" + "liveKEY0123456789abcdef"
        self.assertTrue(make_patch._find_secrets(f'+client = Client(api_key="{fake}")\n'))

    def test_private_key_blocks_the_build(self):
        self.assertTrue(make_patch._find_secrets("+-----BEGIN RSA PRIVATE KEY-----\n"))

    def test_test_assertion_about_masking_is_not_a_leak(self):
        """Заведомо поддельные ключи в тестах — предмет проверки, а не значение."""
        diff = ('+        self.assertNotIn("sk-secret", '
                '_mask_secrets(\'c = Client(api_key="sk-secret-123")\'))\n')
        self.assertEqual(make_patch._find_secrets(diff), [])

    def test_ordinary_code_is_not_a_leak(self):
        self.assertEqual(make_patch._find_secrets("+def read_file(path, offset=1):\n"), [])


class BundleTest(unittest.TestCase):
    """Каталог для переноса копированием."""

    def test_build_directory_is_excluded_from_itself(self):
        """Иначе каждый повторный запуск упаковывал предыдущий результат."""
        self.assertTrue(make_patch.is_excluded("transfer/bundle/files/src/pko/cli.py"))
        self.assertTrue(make_patch.is_excluded("transfer/out/pko-x.patch"))

    def test_custom_output_inside_repo_is_excluded_too(self):
        from pathlib import Path

        own = make_bundle._relative_to_root(make_patch.ROOT / "tmp-bundle")
        self.assertEqual(own, "tmp-bundle/")
        self.assertEqual(make_bundle._relative_to_root(Path("/tmp/elsewhere")), "")

    def test_manifest_lists_files_and_names_deleted_ones(self):
        text = make_bundle._manifest(
            base="0ccd9c56d722af7b92e8656fdaac99991f9dea05",
            carried=[("M", "src/pko/cli.py"), ("A", "src/pko/new.py")],
            deleted=["src/pko/old.py"],
            label="",
        )
        self.assertIn("src/pko/new.py` — новый", text)
        self.assertIn("src/pko/cli.py` — изменён", text)
        # Копирование поверх не удаляет файлы: об этом обязана сказать опись.
        self.assertIn("Удалить вручную", text)
        self.assertIn("src/pko/old.py", text)
        self.assertIn("cp -R files/.", text)

    def test_manifest_without_deletions_has_no_removal_section(self):
        text = make_bundle._manifest(
            base="abc", carried=[("M", "README.md")], deleted=[], label="")
        self.assertNotIn("Удалить вручную", text)


class ManifestTest(unittest.TestCase):
    def test_manifest_names_the_base_commit_and_files(self):
        text = make_patch._manifest(
            base="0ccd9c56d722af7b92e8656fdaac99991f9dea05",
            patch_name="pko-x.patch",
            digest="deadbeef",
            files=["M\tsrc/pko/cli.py", "A\tsrc/pko/new.py"],
            label="отчёты",
        )
        self.assertIn("0ccd9c56d722af7b92e8656fdaac99991f9dea05", text)
        self.assertIn("pko-x.patch", text)
        self.assertIn("src/pko/new.py", text)
        self.assertIn("новых 1", text)
        self.assertIn("git apply --check", text)


if __name__ == "__main__":
    unittest.main()
