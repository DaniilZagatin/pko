"""Мелкие, но принципиальные части: SSH-ссылка, YAML, сторож текста, запрет мутаций git."""

import os
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fixture_support import ensure_fixture
from pko.errors import GitError, PkoError, UrlError
from pko.extractors.base import Fact
from pko.extractors.runner import Extraction
from pko.git.remote import ensure_mirror, mirror_path
from pko.git.repo import GitRepo
from pko.git.url import parse_repo_url
from pko.intent.loader import AUTHORIZATION_REQUIRED_FIELDS, REQUIRED_FIELDS, _parse
from pko.model.schema import PkoModel, PkoObject
from pko.output.publisher import WrittenFile, publish, write_outputs
from pko.report.guard import check_text
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

    def test_folded_block_scalar(self):
        """`>-` — то, чем человек запишет двухфразное поле; раньше файл отвергался целиком."""
        data = loads(
            "business_meaning: >-\n"
            "  Аналитик ведёт проект редизайна.\n"
            "  Данные размазаны по девяти системам.\n"
            "\n"
            "  Нужен советник по методологии.\n"
            "requested_mode: ASSIST\n"
        )
        self.assertEqual(
            data["business_meaning"],
            "Аналитик ведёт проект редизайна. Данные размазаны по девяти системам.\n"
            "Нужен советник по методологии.",
        )
        self.assertEqual(data["requested_mode"], "ASSIST")

    def test_literal_block_scalar_keeps_line_breaks(self):
        data = loads("criteria: |\n  первый пункт\n  второй пункт\nnext: ok\n")
        self.assertEqual(data["criteria"], "первый пункт\nвторой пункт")
        self.assertEqual(data["next"], "ok")

    def test_comment_inside_block_is_content(self):
        """Внутри блока решётка — часть текста, а не комментарий."""
        notes: list[str] = []
        data = loads("note: |\n  задача #1 по HR\n  вторая строка\n", notes)
        self.assertEqual(data["note"], "задача #1 по HR\nвторая строка")
        self.assertEqual(notes, [])

    def test_empty_block_scalar(self):
        data = loads("empty: >-\nnext: 1\n")
        self.assertEqual(data["empty"], "")
        self.assertEqual(data["next"], 1)

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


class IntentOverrideFailureTest(unittest.TestCase):
    """`--intent`, указывающий на нечитаемое, — ошибка запуска, а не свойство намерения.

    Проверялось только существование пути, поэтому каталог доходил до
    `read_text` и ронял прогон трассировкой `IsADirectoryError`: CLI ловит
    `PkoError`, а `OSError` мимо него проходит. Отчёт при этом не выпускался
    вовсе — оператор получал стек вместо причины.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _load(self, path):
        from pko.intent.loader import load_intent

        return load_intent(None, "abc1234", override_path=str(path))

    def test_directory_is_reported_not_raised_as_oserror(self):
        target = self.tmp / "intent_dir"
        target.mkdir()
        with self.assertRaises(PkoError) as ctx:
            self._load(target)
        self.assertIn("не на файл", ctx.exception.message)
        self.assertIn("каталог", ctx.exception.hint)

    def test_missing_file_names_the_way_out(self):
        with self.assertRaises(PkoError) as ctx:
            self._load(self.tmp / "нет-такого.yaml")
        self.assertIn("не найден", ctx.exception.message)
        self.assertIn("--intent", ctx.exception.hint)

    def test_unreadable_file_becomes_a_readable_refusal(self):
        target = self.tmp / "locked.yaml"
        target.write_text("business_owner: A\n", encoding="utf-8")
        target.chmod(0o000)
        self.addCleanup(target.chmod, 0o644)
        if os.access(target, os.R_OK):
            self.skipTest("права не применяются: тест запущен от root")
        with self.assertRaises(PkoError) as ctx:
            self._load(target)
        self.assertIn("не прочитан", ctx.exception.message)
        self.assertIn("права доступа", ctx.exception.hint)

    def test_binary_file_is_refused_with_its_position(self):
        target = self.tmp / "binary.yaml"
        target.write_bytes(b"\xff\xfe\x00\x01business_owner")
        with self.assertRaises(PkoError) as ctx:
            self._load(target)
        self.assertIn("UTF-8", ctx.exception.message)
        self.assertIn("позиции", ctx.exception.hint)

    def test_readable_file_still_loads(self):
        target = self.tmp / "ok.yaml"
        target.write_text("business_owner: Иванова А.А.\n", encoding="utf-8")
        result = self._load(target)
        self.assertEqual(result.data["business_owner"], "Иванова А.А.")


class IntentJsonTest(unittest.TestCase):
    """`business_intent.json` объявлен входом наравне с YAML и обязан читаться.

    Разбор шёл YAML-подмножеством для любого входа: открывающая скобка JSON —
    не «ключ: значение», поэтому полностью заполненное намерение отвергалось
    целиком, и Gate возвращал `NO_DECISION` при подтверждённом владельцем входе.
    """

    PAYLOAD = {
        "confirmed_need_id": "NEED-1",
        "business_owner": "Иванова А.А.",
        "target_state": "результат получен",
        "success_criteria": "есть числа",
        "maturity": "pilot",
        "consequence": "low",
        "requested_mode": "CONFIRM",
        "decision_boundary": "END_TO_END_PROCESS",
        "in_scope": ["чтение данных", "синтез ответа"],
        "forbidden_effects": ["изменение данных", "внешние сообщения"],
    }

    def _json(self, payload=None):
        import json as _json_mod

        text = _json_mod.dumps(self.PAYLOAD if payload is None else payload,
                               ensure_ascii=False, indent=2)
        return _parse(text, "business_intent.json", "abc1234")

    def test_complete_json_intent_is_usable(self):
        result = self._json()
        self.assertEqual(result.error, "")
        self.assertEqual(result.missing, [])
        self.assertTrue(result.complete)
        self.assertEqual(result.data["business_owner"], "Иванова А.А.")

    def test_json_lists_survive_as_lists(self):
        """`in_scope` должен остаться списком: строка «[…]» испортила бы scope записи."""
        self.assertEqual(self._json().data["in_scope"], ["чтение данных", "синтез ответа"])

    def test_empty_forbidden_list_is_not_an_explicit_policy(self):
        result = self._json(dict(self.PAYLOAD, forbidden_effects=[]))
        self.assertFalse(result.complete)
        self.assertIn("forbidden_effects", result.missing)

    def test_none_is_an_explicit_forbidden_effects_policy(self):
        result = self._json(dict(self.PAYLOAD, forbidden_effects="none"))
        self.assertTrue(result.complete, msg=result.problem())

    def test_scope_must_be_text_or_a_nonempty_list_of_text(self):
        result = self._json(dict(self.PAYLOAD, in_scope={"operation": "read"}))
        self.assertFalse(result.usable)
        self.assertTrue(any("in_scope" in item for item in result.invalid))

    def test_enums_are_checked_the_same_way(self):
        payload = dict(self.PAYLOAD, consequence="hgh")
        result = self._json(payload)
        self.assertFalse(result.usable)
        self.assertTrue(any("consequence" in i for i in result.invalid))

    def test_broken_json_names_the_line(self):
        result = _parse('{"a": 1,,}', "business_intent.json", "abc1234")
        self.assertFalse(result.usable)
        self.assertIn("строка 1", result.problem())
        self.assertIn("business_intent.json", result.problem(),
                      msg="сообщение должно указывать на тот файл, который правят")

    def test_json_array_is_not_a_set_of_fields(self):
        result = _parse('["a", "b"]', "business_intent.json", "abc1234")
        self.assertIn("ключ: значение", result.error)

    def test_yaml_is_still_parsed_as_yaml(self):
        """Выбор разбора идёт по суффиксу источника, а не по содержимому."""
        result = _parse("confirmed_need_id: NEED-1\n", "business_intent.yaml", "abc1234")
        self.assertEqual(result.error, "")
        self.assertEqual(result.data["confirmed_need_id"], "NEED-1")

    def test_repository_json_is_found_and_read(self):
        """Путь из SEARCH_PATHS должен работать целиком, а не только для YAML."""
        import json as _json_mod

        from pko.intent.loader import load_intent

        class _Tree:
            def read(self, path):
                if path == "business_intent.json":
                    return _json_mod.dumps(IntentJsonTest.PAYLOAD, ensure_ascii=False)
                return None

        result = load_intent(_Tree(), "abc1234")
        self.assertEqual(result.source, "business_intent.json")
        self.assertTrue(result.complete)

    def test_external_json_intent_keeps_its_suffix(self):
        """`--intent x.json` опознаётся по переносимому имени, а оно хранит суффикс."""
        import json as _json_mod
        import tempfile

        from pko.intent.loader import load_intent

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "intent.json"
            path.write_text(_json_mod.dumps(self.PAYLOAD, ensure_ascii=False),
                            encoding="utf-8")
            result = load_intent(None, "abc1234", override_path=str(path))
        self.assertTrue(result.source.endswith(".json"))
        self.assertTrue(result.complete, msg=result.problem())


class IntentEnumTest(unittest.TestCase):
    """Опечатка в перечислимом поле не должна тихо смягчать классификацию риска."""

    BASE = (
        "confirmed_need_id: NEED-1\n"
        "business_owner: Иванова А.А.\n"
        "target_state: результат получен\n"
        "success_criteria: есть числа\n"
        "decision_boundary: END_TO_END_PROCESS\n"
        "in_scope: чтение данных\n"
        "forbidden_effects: изменение данных\n"
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

    def test_result_fields_without_authorization_do_not_enable_a_decision(self):
        result = _parse(
            "confirmed_need_id: NEED-1\n"
            "business_owner: Иванова А.А.\n"
            "target_state: результат получен\n"
            "success_criteria: есть числа\n",
            "business_intent.yaml",
            "abc1234",
        )
        self.assertFalse(result.complete)
        self.assertEqual(set(result.missing), set(AUTHORIZATION_REQUIRED_FIELDS))

    def test_template_with_only_comments_is_incomplete(self):
        """Незаполненный шаблон — это отсутствие входа, а не заполненный файл."""
        result = _parse(
            "# business_intent.yaml\n# заполните поля ниже\n",
            "business_intent.yaml",
            "abc1234",
        )
        self.assertFalse(result.complete)
        self.assertEqual(len(result.missing), len(REQUIRED_FIELDS))
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

    def test_external_source_distinguishes_equal_basenames(self):
        from pko.intent.loader import external_source

        path = Path("business_intent.yaml")
        first = external_source(path, "business_owner: A\n")
        second = external_source(path, "business_owner: B\n")
        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^external/business_intent-[0-9a-f]{12}\.yaml$")


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


class TaxonomyTest(unittest.TestCase):
    """Двухуровневая таксономия: универсальный смысл плюс механизм."""

    def test_every_legacy_kind_has_facets(self):
        """Вид без разложения молча выпал бы из модели: предикаты его не увидят."""
        from pko.extractors.base import FACT_KINDS
        from pko.model import taxonomy

        for kind in FACT_KINDS:
            if kind in taxonomy.CATEGORIES:
                continue
            with self.subTest(kind=kind):
                facets = taxonomy.facets_for(kind)
                self.assertIn(facets.category, taxonomy.CATEGORIES)
                self.assertNotEqual(facets.category, taxonomy.UNKNOWN,
                                    msg=f"{kind} не разложен на фасеты")

    def test_sql_is_a_mechanism_not_a_category(self):
        from pko.model import taxonomy

        write = taxonomy.facets_for("SQL_WRITE")
        self.assertEqual(write.category, taxonomy.EFFECT)
        self.assertEqual(write.mechanism, "sql")
        self.assertTrue(taxonomy.is_mutating(write))
        # Тот же смысл другим механизмом — та же категория и действие.
        orm = taxonomy.Facets(taxonomy.EFFECT, "write", "orm")
        self.assertTrue(taxonomy.is_effect(orm))

    def test_mechanism_spelling_is_normalized(self):
        """Без приведения `Postgres` и `sql` стали бы разными механизмами."""
        from pko.model import taxonomy

        for raw in ("SQL", " sql ", "Postgres", "postgresql", "MySQL"):
            with self.subTest(raw=raw):
                self.assertEqual(taxonomy.normalize_mechanism(raw), "sql")
        self.assertEqual(taxonomy.normalize_mechanism("React"), "ui_event")
        self.assertEqual(taxonomy.normalize_mechanism("Smart Contract"), "smart_contract")

    def test_gate_data_perimeter_covers_sql_and_orm(self):
        """Периметр повторяет прежний: `session.add` роняло проверку и должно ронять.

        До разделения признаков конструкции ORM входили в шаблон `SQL_WRITE`.
        Сузить периметр до одного SQL значило бы молча выпустить репозиторий
        на SQLAlchemy, изменения данных в котором агент нашёл.
        """
        from pko.model import taxonomy

        self.assertEqual(set(taxonomy.GATE_DATA_MECHANISMS), {"sql", "orm"})
        self.assertTrue(taxonomy.is_mutating(taxonomy.Facets(taxonomy.EFFECT, "write", "orm")))
        # Файлы и хранилища в вердикт не входят — это было бы расширением.
        self.assertFalse(taxonomy.is_mutating(taxonomy.Facets(taxonomy.EFFECT, "write", "fs")))


class FacetConsistencyTest(unittest.TestCase):
    """Признаки проверяются как целое, а не по отдельности."""

    def test_legacy_facets_satisfy_the_consistency_table(self):
        """Таблица допустимых сочетаний обязана согласоваться с псевдонимами.

        Разойдись они — и прежний вид отклонялся бы собственной проверкой.
        """
        from pko.model import taxonomy

        for kind, facets in taxonomy.LEGACY_FACETS.items():
            with self.subTest(kind=kind):
                self.assertEqual(taxonomy.conflict(facets), "",
                                 msg=f"{kind} не проходит собственную проверку")

    def test_mechanism_dictates_category(self):
        from pko.model import taxonomy

        write_as_entrypoint = taxonomy.Facets(taxonomy.ENTRYPOINT, "write", "fs")
        self.assertIn("не бывает категории", taxonomy.conflict(write_as_entrypoint))
        self.assertEqual(taxonomy.conflict(taxonomy.Facets(taxonomy.EFFECT, "write", "fs")), "")

    def test_unknown_mechanism_is_not_constrained(self):
        """Незнакомый механизм и так не влияет на вердикт — запрещать его нечем."""
        from pko.model import taxonomy

        self.assertEqual(
            taxonomy.conflict(taxonomy.Facets(taxonomy.EFFECT, "call", "smart_contract")), "")

    def test_legacy_kind_cannot_be_overridden(self):
        from pko.model import taxonomy

        conflict = taxonomy.legacy_conflict(
            "SQL_WRITE", taxonomy.Facets(taxonomy.ENTRYPOINT, "", ""))
        self.assertIn("вид SQL_WRITE означает", conflict)
        # Повтор того же смысла конфликтом не является.
        self.assertEqual(
            taxonomy.legacy_conflict("SQL_WRITE", taxonomy.Facets(taxonomy.EFFECT, "write", "sql")),
            "")


class ExtractorFacetTest(unittest.TestCase):
    """Факты экстрактора обязаны проходить ту же проверку сочетаний, что и агентские."""

    def test_every_extracted_fact_is_self_consistent(self):
        from pko.extractors.base import Tree
        from pko.extractors.runner import extract_all
        from pko.git.repo import GitRepo
        from pko.model import taxonomy

        repo = GitRepo(ensure_fixture())
        tree = Tree.at(repo, repo.resolve("master"))
        for fact in extract_all(tree).facts:
            with self.subTest(kind=fact.kind, path=fact.path):
                self.assertEqual(
                    taxonomy.conflict(fact.facets), "",
                    msg=f"экстрактор порождает сочетание, которое сам же запрещает: {fact.key}",
                )


class ReportGuardScopeTest(unittest.TestCase):
    """Сторож текста обязан видеть пути тех стеков, ради которых всё делалось."""

    def test_foreign_stack_paths_are_checked(self):
        from pko.report.guard import check_text

        invented = "Блок собирает экран из ui/src/OrderForm.jsx и воркера workers/main.go."
        violations = check_text(invented, allowed_ids=set(), allowed_paths=set())
        codes = {v.code for v in violations}
        self.assertIn("UNKNOWN_PATH", codes,
                      msg="выдуманный путь чужого стека проходил незамеченным")
        detail = next(v.detail for v in violations if v.code == "UNKNOWN_PATH")
        self.assertIn("OrderForm.jsx", detail)
        self.assertIn("main.go", detail)

    def test_known_path_passes(self):
        from pko.report.guard import check_text

        text = "Экран описан в ui/src/OrderForm.jsx."
        self.assertEqual(
            check_text(text, allowed_ids=set(), allowed_paths={"ui/src/OrderForm.jsx"}), [])


class ChatClientFailFastTest(unittest.TestCase):
    """Пустой ответ модели — отказ, а не тихая подмена шаблоном."""

    def _client(self, tmp: Path, payload: dict):
        from pko.llm.client import ChatClient
        from pko.llm.registry import ModelSpec

        spec = ModelSpec(role="matcher", base_url="https://stub.local/v1",
                         model="stub", api_key="x")
        client = ChatClient(spec=spec, cache_dir=tmp)
        # `_request` — настоящий метод класса, а не что-то добавленное тестом:
        # `delattr` после теста удалял бы его насовсем для всех, кто идёт по
        # алфавиту следом (нашёл `test_web_app.py`, который зависит от него).
        original = ChatClient._request
        ChatClient._request = lambda self, m, p, b: payload
        self.addCleanup(setattr, ChatClient, "_request", original)
        return client

    def test_empty_content_raises_instead_of_returning_nothing(self):
        from pko.errors import LlmError

        with tempfile.TemporaryDirectory() as raw:
            client = self._client(Path(raw), {
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}]})
            with self.assertRaises(LlmError) as ctx:
                client.complete(system="s", user="u")
            self.assertIn("пустой ответ", ctx.exception.message)
            self.assertIn("max_tokens", ctx.exception.hint)

    def test_reasoning_content_is_not_published_as_the_answer(self):
        from pko.errors import LlmError

        with tempfile.TemporaryDirectory() as raw:
            client = self._client(Path(raw), {"choices": [{"message": {
                "content": None, "reasoning_content": "разбор системы"}}]})
            with self.assertRaises(LlmError):
                client.complete(system="s", user="u")

    def test_empty_answer_is_not_cached(self):
        """Иначе повторный прогон достаёт пустоту из кеша и ведёт себя так же."""
        from pko.errors import LlmError

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            client = self._client(tmp, {"choices": [{"message": {"content": "   "}}]})
            with self.assertRaises(LlmError):
                client.complete(system="s", user="u")
            self.assertEqual(list(tmp.glob("*.txt")), [], msg="пустое в кеш не попадает")

    def test_default_budget_leaves_room_for_the_answer(self):
        from pko.llm.client import DEFAULT_MAX_TOKENS

        self.assertGreaterEqual(DEFAULT_MAX_TOKENS, 8192,
                                msg="при включённом рассуждении 2000 уходило в рассуждение")


class MigrationOriginTest(unittest.TestCase):
    """Изменение данных в миграции остаётся нарушением, но названо отдельно."""

    def test_migration_paths_are_recognised(self):
        from pko.extractors.base import is_migration

        self.assertTrue(is_migration("backend/alembic/versions/6fb_default.py"))
        self.assertTrue(is_migration("app/migrations/0001_initial.py"))
        self.assertFalse(is_migration("app/services/orders.py"))


class PerimeterTest(unittest.TestCase):
    """Покрытие считается по backend-периметру, фронтенд назван отдельно."""

    def test_frontend_is_outside_the_perimeter(self):
        from pko.extractors.runner import is_out_of_perimeter

        for path in ("ui/src/App.tsx", "ui/src/app.ts", "web/style.css", "logo.svg"):
            with self.subTest(path=path):
                self.assertTrue(is_out_of_perimeter(path))
        self.assertFalse(is_out_of_perimeter("app/main.py"))
        self.assertFalse(is_out_of_perimeter("pyproject.toml"))

    def test_frontend_is_not_repeated_in_skipped_summary(self):
        from pko.extractors.base import Tree
        from pko.extractors.runner import _skipped_summary

        tree = Tree(repo=None, sha="x", files=["frontend/App.tsx", "app/main.py"])
        self.assertEqual(_skipped_summary(tree, ["app/main.py"]), [])


if __name__ == "__main__":
    unittest.main()
