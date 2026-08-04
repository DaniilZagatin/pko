"""Мелкие, но принципиальные части: SSH-ссылка, YAML, сторож текста, запрет мутаций git."""

import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fixture_support import EXAMPLE_INTENT, ensure_fixture
from pko.assemble.candidates import Candidate
from pko.assemble.heuristic import _build_guardrails
from pko.errors import GitError, PkoError, UrlError
from pko.extractors.base import Fact
from pko.extractors.runner import Extraction
from pko.git.remote import ensure_mirror, mirror_path
from pko.git.repo import GitRepo
from pko.git.url import parse_repo_url
from pko.intent.loader import _parse
from pko.model.schema import PkoModel, PkoObject
from pko.output.publisher import WrittenFile, publish, write_outputs
from pko.report.guard import check_text
from pko.report.writer import _allowed_paths, _model_digest
from pko.util.yamlmini import YamlSubsetError, loads


class UrlTest(unittest.TestCase):
    def test_ssh_scheme_with_port(self):
        ref = parse_repo_url(
            "ssh://git@stash.delta.sbrf.ru:7999/datacore_ai/ai-agent-deepresearch.git"
        )
        self.assertEqual(ref.host, "stash.delta.sbrf.ru")
        self.assertEqual(ref.port, 7999)
        self.assertEqual(ref.project, "datacore_ai")
        self.assertEqual(ref.repo, "ai-agent-deepresearch")
        self.assertEqual(ref.mirror_dirname, "ai-agent-deepresearch.git")

    def test_scp_like_form(self):
        ref = parse_repo_url("git@stash.delta.sbrf.ru:datacore_ai/ai-agent-deepresearch.git")
        self.assertEqual(ref.project, "datacore_ai")
        self.assertEqual(ref.repo, "ai-agent-deepresearch")
        self.assertIsNone(ref.port)

    def test_https_is_rejected_with_hint(self):
        with self.assertRaises(UrlError) as ctx:
            parse_repo_url("https://stash.delta.sbrf.ru/scm/proj/repo.git")
        self.assertIn("ssh://", ctx.exception.hint)

    def test_cache_path_traversal_is_rejected(self):
        with self.assertRaises(UrlError):
            parse_repo_url("ssh://git@stash.local:7999/../repo.git")

    def test_cache_identity_includes_user_and_port(self):
        base = parse_repo_url("ssh://git@stash.local:7999/proj/repo.git")
        other_port = parse_repo_url("ssh://git@stash.local:8022/proj/repo.git")
        other_user = parse_repo_url("ssh://svc@stash.local:7999/proj/repo.git")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.assertNotEqual(mirror_path(base, root), mirror_path(other_port, root))
            self.assertNotEqual(mirror_path(base, root), mirror_path(other_user, root))

    def test_existing_mirror_origin_must_match(self):
        requested = "ssh://git@stash.local:7999/proj/repo.git"
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dest = mirror_path(parse_repo_url(requested), root)
            dest.parent.mkdir(parents=True)
            subprocess.run(["git", "init", "--bare", str(dest)], check=True,
                           capture_output=True)
            subprocess.run(
                ["git", "-C", str(dest), "remote", "add", "origin",
                 "ssh://git@stash.local:7999/proj/other.git"],
                check=True, capture_output=True,
            )
            with self.assertRaises(GitError) as ctx:
                ensure_mirror(requested, cache_root=root, fetch=False)
            self.assertIn("другому remote", ctx.exception.message)


class YamlTest(unittest.TestCase):
    def test_flat_keys_lists_and_nesting(self):
        data = loads(
            "confirmed_need_id: NEED-HR-001\n"
            "requested_mode: CONFIRM\n"
            "success_criteria:\n"
            "  - есть числа из базы\n"
            "  - указаны таблицы\n"
            "owner:\n"
            "  name: Иванова А.А.\n"
            "  role: владелец продукта\n"
            "# комментарий\n"
            "maturity: pilot\n"
        )
        self.assertEqual(data["confirmed_need_id"], "NEED-HR-001")
        self.assertEqual(len(data["success_criteria"]), 2)
        self.assertEqual(data["owner"]["role"], "владелец продукта")
        self.assertEqual(data["maturity"], "pilot")

    def test_scalars(self):
        data = loads("port: 8000\nratio: 0.75\nenabled: да\nempty: null\nquoted: \"да: нет\"\n")
        self.assertEqual(data["port"], 8000)
        self.assertEqual(data["ratio"], 0.75)
        self.assertIs(data["enabled"], True)
        self.assertIsNone(data["empty"])
        self.assertEqual(data["quoted"], "да: нет")

    def test_tabs_are_rejected(self):
        with self.assertRaises(YamlSubsetError):
            loads("a:\n\tb: 1\n")

    def test_hash_inside_quotes_survives(self):
        """`"a #b"` — значение целиком; раньше кавычка обрывалась на решётке."""
        notes: list[str] = []
        data = loads(
            "quoted: \"a #b\"\n"
            "single: 'отчёт #1'\n"
            "url: https://example.local/page#anchor\n"
            "with_comment: значение  # пояснение\n",
            notes,
        )
        self.assertEqual(data["quoted"], "a #b")
        self.assertEqual(data["single"], "отчёт #1")
        # Решётка без пробела перед ней — часть значения, как и в YAML.
        self.assertEqual(data["url"], "https://example.local/page#anchor")
        self.assertEqual(data["with_comment"], "значение")

    def test_truncation_by_comment_is_reported(self):
        """Правило YAML сохранено, но потеря части значения больше не молчаливая."""
        notes: list[str] = []
        data = loads("need_name: Отчёт #1 по HR\n", notes)
        self.assertEqual(data["need_name"], "Отчёт")
        self.assertTrue(notes, "пользователь должен увидеть предупреждение")
        self.assertIn("кавычки", notes[0])


class GuardTest(unittest.TestCase):
    def test_known_ids_pass(self):
        violations = check_text(
            "Блок BBB-001 использует операцию AO-002.",
            allowed_ids={"BBB-001", "AO-002"},
        )
        self.assertEqual(violations, [])

    def test_invented_id_is_caught(self):
        violations = check_text("Появился блок BBB-099.", allowed_ids={"BBB-001"})
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].code, "UNKNOWN_ID")

    def test_invented_path_is_caught(self):
        violations = check_text(
            "Ограничение задано в backend/src/secret.py.",
            allowed_ids=set(),
            allowed_paths={"backend/src/config/settings.py"},
        )
        self.assertEqual(violations[0].code, "UNKNOWN_PATH")

    def test_check_ids_are_allowed(self):
        self.assertEqual(check_text("Проверка CHK-NEED-001 не пройдена.", allowed_ids=set()), [])

    def test_writer_may_repeat_paths_shown_in_digest(self):
        model = PkoModel(gaps=["Не найден business_intent.yaml"])
        obj = PkoObject(id="GRD-001", kind="GUARDRAIL", name="таймаут")
        obj.set("Точка применения", ["backend/a.py", "backend/b.py"], "OBSERVED", [])
        model.add(obj)
        import json
        user = json.dumps(_model_digest(model), ensure_ascii=False)
        allowed = _allowed_paths(model, user)
        text = "Не найден business_intent.yaml; точки: backend/a.py и backend/b.py."
        self.assertEqual(check_text(text, model.ids(), allowed), [])


class GuardrailAggregationTest(unittest.TestCase):
    def test_all_timeout_values_and_paths_are_reported(self):
        facts = [
            Fact("LIMIT", "timeout", 5, "backend/a.py", 10, "timeout = 5"),
            Fact("LIMIT", "timeout", 600, "backend/b.py", 20, "timeout = 600"),
        ]
        candidate = Candidate(
            id="limit:timeout", type="CONSTRAINT", subtype="LIMIT",
            name="timeout", group="backend", facts=facts,
        )
        model = PkoModel()
        guards = _build_guardrails(model, [candidate], Extraction(facts=facts), "abc12345")
        guard = guards[0]
        self.assertIn("5", guard.get_text("Значение"))
        self.assertIn("600", guard.get_text("Значение"))
        points = guard.fields["Точка применения"].value
        self.assertEqual(points, ["backend/a.py", "backend/b.py"])


class IntentEnumTest(unittest.TestCase):
    """Опечатка в перечислимом поле не должна тихо смягчать классификацию риска."""

    BASE = (
        "confirmed_need_id: NEED-1\n"
        "business_owner: Иванова А.А.\n"
        "target_state: результат получен\n"
        "success_criteria: есть числа\n"
    )

    def _load(self, extra: str):
        return _parse(self.BASE + extra, "business_intent.yaml", "abc1234")

    def test_valid_enums_are_usable(self):
        result = self._load("maturity: pilot\nconsequence: low\nrequested_mode: CONFIRM\n")
        self.assertEqual(result.invalid, [])
        self.assertTrue(result.usable)
        self.assertTrue(result.complete)

    def test_typo_makes_intent_unusable(self):
        result = self._load("consequence: hgh\n")
        self.assertTrue(result.present, "файл прочитан")
        self.assertFalse(result.usable, "но к решению Gate не допускается")
        self.assertTrue(any("consequence" in i for i in result.invalid))
        self.assertIn("недопустимые значения", result.problem())

    def test_unknown_mode_is_rejected(self):
        self.assertFalse(self._load("requested_mode: SEMI_AUTO\n").usable)

    def test_case_and_spacing_are_tolerated(self):
        result = self._load("maturity: Pilot\nrequested_mode: confirm\nscale: LOCAL\n")
        self.assertEqual(result.invalid, [])

    def test_empty_enum_is_not_an_error(self):
        result = self._load("maturity:\n")
        self.assertEqual(result.invalid, [])

    def test_template_with_only_comments_is_incomplete(self):
        """Незаполненный шаблон — это отсутствие входа, а не заполненный файл."""
        result = _parse(
            "# business_intent.yaml\n# заполните поля ниже\n",
            "business_intent.yaml",
            "abc1234",
        )
        self.assertFalse(result.complete)
        self.assertEqual(len(result.missing), 4)
        self.assertIn("не заполнены обязательные поля", result.problem())

    def test_partially_filled_file_is_incomplete(self):
        result = _parse(
            "confirmed_need_id: NEED-1\nbusiness_owner: Иванова А.А.\n",
            "business_intent.yaml",
            "abc1234",
        )
        self.assertTrue(result.usable, "перечни не нарушены")
        self.assertFalse(result.complete, "но обязательные поля не заполнены")
        self.assertIn("target_state", result.problem())

    def test_template_comments_do_not_produce_warnings(self):
        """Выровненный комментарий у значения — обычная запись, а не потеря данных."""
        sample = EXAMPLE_INTENT
        self.assertTrue(sample.exists())
        result = _parse(sample.read_text(encoding="utf-8"), str(sample), "abc1234")
        self.assertEqual(
            result.warnings, [],
            msg=f"шаблон проекта не должен порождать предупреждений: {result.warnings}",
        )
        self.assertTrue(result.complete, "образец заполнен полностью")


class PublishGuardTest(unittest.TestCase):
    """Публикация перезаписывает файлы по фиксированным именам — каталог должен быть тем самым."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        source = self.root / "src"
        source.mkdir()
        report = source / "taxonomy.html"
        report.write_text("<html></html>", encoding="utf-8")
        self.written = [WrittenFile(kind="taxonomy", path=report)]

    def test_refuses_foreign_directory(self):
        foreign = self.root / "site-packages"
        foreign.mkdir()
        (foreign / "some_library.py").write_text("x = 1", encoding="utf-8")
        with self.assertRaises(PkoError) as ctx:
            publish(self.written, foreign)
        self.assertIn("не похож", ctx.exception.message)

    def test_publishes_into_reports_directory(self):
        target = self.root / "reports"
        target.mkdir()
        (target / "taxonomy_v1_1.html").write_text("старое", encoding="utf-8")
        actions = publish(self.written, target)
        self.assertTrue(any("опубликовано" in a for a in actions))
        self.assertEqual((target / "taxonomy_v1_1.html").read_text(encoding="utf-8"),
                         "<html></html>")
        self.assertTrue((target / "taxonomy_v1_1.html.bak").exists(), "старое сохранено")

    def test_empty_directory_is_allowed(self):
        target = self.root / "fresh"
        target.mkdir()
        self.assertTrue(publish(self.written, target))

    def test_failed_commit_restores_entire_previous_set(self):
        import pko.output.publisher as publisher

        source = self.root / "src"
        passports = source / "passports.html"
        passports.write_text("новые паспорта", encoding="utf-8")
        written = self.written + [WrittenFile(kind="passports", path=passports)]

        target = self.root / "reports"
        target.mkdir()
        taxonomy_target = target / "taxonomy_v1_1.html"
        passports_target = target / "passports_v1_1.html"
        taxonomy_target.write_text("старая таксономия", encoding="utf-8")
        passports_target.write_text("старые паспорта", encoding="utf-8")

        real_replace = publisher._replace
        failed = False

        def flaky(source_path, target_path):
            nonlocal failed
            if target_path.name == "passports_v1_1.html" and not failed:
                failed = True
                raise OSError("fault injection")
            return real_replace(source_path, target_path)

        with patch("pko.output.publisher._replace", side_effect=flaky):
            with self.assertRaises(PkoError):
                publish(written, target)
        self.assertEqual(taxonomy_target.read_text(encoding="utf-8"), "старая таксономия")
        self.assertEqual(passports_target.read_text(encoding="utf-8"), "старые паспорта")

    def test_failed_generation_commit_restores_output_set(self):
        import pko.output.publisher as publisher

        out = self.root / "out"
        out.mkdir()
        first = out / "first.md"
        second = out / "second.md"
        first.write_text("старый первый", encoding="utf-8")
        second.write_text("старый второй", encoding="utf-8")
        real_replace = publisher._replace
        failed = False

        def flaky(source_path, target_path):
            nonlocal failed
            if target_path.name == "second.md" and not failed:
                failed = True
                raise OSError("fault injection")
            return real_replace(source_path, target_path)

        with patch("pko.output.publisher._replace", side_effect=flaky):
            with self.assertRaises(PkoError):
                write_outputs(
                    out,
                    {"first": ("first.md", "новый первый"),
                     "second": ("second.md", "новый второй")},
                )
        self.assertEqual(first.read_text(encoding="utf-8"), "старый первый")
        self.assertEqual(second.read_text(encoding="utf-8"), "старый второй")


class RepoGuardTest(unittest.TestCase):
    def test_mutating_subcommand_is_blocked(self):
        repo = GitRepo(ensure_fixture())
        for forbidden in ("checkout", "reset", "push", "commit"):
            with self.subTest(cmd=forbidden), self.assertRaises(GitError) as ctx:
                repo.run(forbidden, "--help")
            self.assertIn("запрещена", ctx.exception.message)

    def test_read_commands_work(self):
        repo = GitRepo(ensure_fixture())
        sha = repo.resolve("master")
        self.assertEqual(len(sha), 40)
        self.assertIn("backend/src/config/settings.py", repo.files(sha))
        self.assertIsNone(repo.read_text(sha, "не-существует.py"))


if __name__ == "__main__":
    unittest.main()
