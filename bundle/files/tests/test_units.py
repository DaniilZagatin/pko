"""Мелкие, но принципиальные части: SSH-ссылка, YAML, сторож текста, запрет мутаций git."""

import os
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


class ModelKeyTest(unittest.TestCase):
    """Ключ роли не подменяется чужим, если названная переменная пуста."""

    def test_named_env_missing_is_an_error_not_a_fallback(self):
        from pko.llm.registry import get_spec

        os.environ["PKO_ASSEMBLER_API_KEY"] = "внутренний-ключ"
        self.addCleanup(os.environ.pop, "PKO_ASSEMBLER_API_KEY", None)
        os.environ.pop("PKO_SCOUT_MISSING_KEY", None)

        with self.assertRaises(PkoError) as ctx:
            get_spec("scout", base_url="https://внешний.example",
                     api_key_env="PKO_SCOUT_MISSING_KEY",
                     allowed_hosts="внешний.example")
        self.assertIn("PKO_SCOUT_MISSING_KEY", ctx.exception.message)

    def test_named_env_present_is_used(self):
        from pko.llm.registry import get_spec

        os.environ["PKO_SCOUT_TEST_KEY"] = "внешний-ключ"
        self.addCleanup(os.environ.pop, "PKO_SCOUT_TEST_KEY", None)
        spec = get_spec("scout", base_url="https://внешний.example",
                        api_key_env="PKO_SCOUT_TEST_KEY",
                        allowed_hosts="внешний.example")
        self.assertEqual(spec.api_key, "внешний-ключ")

    def test_scout_endpoint_is_default_deny_without_host_allowlist(self):
        from pko.llm.registry import get_spec

        with patch.dict(os.environ, {"PKO_SCOUT_ALLOWED_HOSTS": ""}, clear=False):
            with self.assertRaises(PkoError) as ctx:
                get_spec("scout", base_url="https://public.example/v1")
        self.assertIn("allowlist", ctx.exception.message)

    def test_scout_endpoint_must_match_exact_allowed_host(self):
        from pko.llm.registry import get_spec

        with self.assertRaises(PkoError):
            get_spec(
                "scout", base_url="https://public.example/v1",
                allowed_hosts="llm.company.local",
            )
        spec = get_spec(
            "scout", base_url="https://llm.company.local:8443/v1",
            allowed_hosts="llm.company.local",
        )
        self.assertEqual(spec.base_url, "https://llm.company.local:8443/v1")


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

    def test_static_sqlalchemy_import_does_not_enter_the_check(self):
        """Импорт `sqlalchemy` — обращение, а не изменение: вердикт от него не зависит."""
        from pko.extractors.base import Fact
        from pko.extractors.runner import Extraction
        from pko.gate import policies

        imported = Fact(kind="EXTERNAL", key="Реляционная БД через SQLAlchemy",
                        value="sqlalchemy", path="a.py", line=1, mechanism="orm")
        extraction = Extraction(facts=[imported])
        self.assertEqual(policies.data_writes(extraction), [])
        self.assertEqual(policies.data_reads(extraction), [])

    def test_writes_outside_the_perimeter_are_named_in_gaps(self):
        """Вердикт их не учитывает, поэтому отчёт обязан сказать о них прямо."""
        from pko.extractors.base import Fact
        from pko.extractors.runner import Extraction
        from pko.gate import policies

        write = Fact(kind="EFFECT", key="выгрузка", value="x", path="a.py", line=1,
                     category="EFFECT", action="write", mechanism="fs")
        found = policies.unguarded_writes(Extraction(facts=[write]))
        self.assertEqual([f.path for f in found], ["a.py"])


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

    def test_every_verifiable_mechanism_has_allowed_categories(self):
        """Механизм с проверкой, но без правила сочетаний, снова принимал бы категорию на слово."""
        from pko.agent import verifiers
        from pko.model import taxonomy

        for mechanism in verifiers.covered_mechanisms():
            with self.subTest(mechanism=mechanism):
                self.assertIn(mechanism, taxonomy.MECHANISM_CATEGORIES)


class DirectionalEvidenceTest(unittest.TestCase):
    """Доказательство обязано совпадать по направлению с заявленным действием."""

    def _accepted(self, category, action, mechanism, lines):
        from pko.agent import verifiers
        from pko.model.taxonomy import Facets

        return not verifiers.mismatch(Facets(category, action, mechanism), lines, 1)

    def test_producer_call_does_not_prove_a_consumer(self):
        self.assertFalse(self._accepted("ENTRYPOINT", "serve", "queue",
                                        ["self.broker.send('orders', message)"]))
        self.assertTrue(self._accepted("ENTRYPOINT", "serve", "queue",
                                       ["for m in self.broker.consume('orders'):"]))

    def test_edge_does_not_prove_a_node(self):
        self.assertFalse(self._accepted("STEP", "", "graph", ["g.add_edge('a', 'b')"]))
        self.assertTrue(self._accepted("STEP", "", "graph", ["g.add_node('a', run)"]))
        self.assertTrue(self._accepted("STATE", "transition", "graph", ["g.add_edge('a', 'b')"]))

    def test_read_does_not_prove_a_write(self):
        self.assertFalse(self._accepted("EFFECT", "write", "state_store", ["cache.get('k')"]))
        self.assertTrue(self._accepted("EFFECT", "write", "state_store", ["cache.set('k', v)"]))

    def test_limit_name_matches_whole_word_not_substring(self):
        self.assertFalse(self._accepted("CONTROL", "", "limit", ["generated_at = 1"]))
        self.assertTrue(self._accepted("CONTROL", "", "limit", ["db_max_rows = 100"]))

    def test_generic_verb_needs_a_data_receiver(self):
        self.assertFalse(self._accepted("EFFECT", "write", "orm", ["opts.update(values)"]))
        self.assertTrue(self._accepted("EFFECT", "write", "orm", ["session.add(order)"]))

    def test_uncovered_combination_is_not_gate_eligible(self):
        """Нет подходящего шаблона — наблюдение остаётся, но вердикта не касается."""
        from pko.agent import verifiers
        from pko.model.taxonomy import Facets

        self.assertFalse(verifiers.is_covered(Facets("EFFECT", "read", "http_client")))
        self.assertEqual(verifiers.mismatch(Facets("EFFECT", "read", "http_client"), ["x = 1"], 1), "")

    def test_gate_constructions_inside_strings_are_not_code(self):
        from pko.agent import verifiers
        from pko.model.taxonomy import Facets

        route = Facets("ENTRYPOINT", "serve", "http_server")
        node = Facets("STEP", "", "graph")
        self.assertTrue(verifiers.mismatch(route, ['"""app.route(\'/fake\')"""'], 1))
        self.assertTrue(verifiers.mismatch(node, ['"""graph.add_node(\'fake\', fn)"""'], 1))

    def test_agent_sql_regex_is_never_gate_evidence(self):
        from pko.agent import verifiers
        from pko.model.taxonomy import Facets

        sql = Facets("EFFECT", "write", "sql")
        self.assertEqual(verifiers.mismatch(sql, ['"""DELETE FROM users"""'], 1), "")
        self.assertFalse(verifiers.is_gate_eligible(sql))


class ExtractorFacetTest(unittest.TestCase):
    """Факты экстрактора обязаны проходить ту же проверку сочетаний, что и агентские."""

    def test_every_extracted_fact_is_self_consistent(self):
        from fixture_support import ensure_fixture, ensure_multistack_fixture
        from pko.extractors.base import Tree
        from pko.extractors.runner import extract_all
        from pko.git.repo import GitRepo
        from pko.model import taxonomy

        for path in (ensure_fixture(), ensure_multistack_fixture()):
            repo = GitRepo(path)
            tree = Tree.at(repo, repo.resolve("master"))
            for fact in extract_all(tree).facts:
                with self.subTest(repo=path.name, kind=fact.kind, path=fact.path):
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

    def test_suffix_list_has_one_source(self):
        """Три места опираются на один перечень: стек, покрытие и сторож текста."""
        from pko.agent.stack import MARKER_EXT, UNPARSED_CODE_EXT
        from pko.extractors.base import CODE_SUFFIXES, DATA_SUFFIXES
        from pko.report.guard import PATH_PATTERN

        self.assertEqual(set(MARKER_EXT), set(CODE_SUFFIXES) | set(DATA_SUFFIXES))
        self.assertTrue(set(UNPARSED_CODE_EXT) < set(CODE_SUFFIXES))
        self.assertNotIn(".py", UNPARSED_CODE_EXT, msg="Python разбирается статически")
        for suffix in CODE_SUFFIXES + DATA_SUFFIXES:
            with self.subTest(suffix=suffix):
                self.assertTrue(PATH_PATTERN.search(f"src/file{suffix}"))


class GatePolicyTest(unittest.TestCase):
    """Наблюдение без структурной проверки не участвует в решении о допуске."""

    def _extraction(self, *facts):
        from pko.extractors.runner import Extraction

        return Extraction(facts=list(facts))

    def test_unverifiable_observation_is_excluded(self):
        from pko.extractors.base import Fact
        from pko.gate import policies

        trusted = Fact(kind="SQL_WRITE", key="sql", value="x", path="a.py", line=1)
        agent_claim = Fact(kind="EFFECT", key="запись", value="x", path="b.py", line=1,
                           category="EFFECT", action="write", mechanism="sql",
                           gate_eligible=False)
        extraction = self._extraction(trusted, agent_claim)

        writes = policies.data_writes(extraction)
        self.assertEqual([f.path for f in writes], ["a.py"])
        self.assertEqual([f.path for f in policies.unverifiable(extraction)], ["b.py"])

    def test_trajectory_accepts_any_supported_mechanism(self):
        from pko.extractors.base import Fact
        from pko.gate import policies

        cli_step = Fact(kind="STEP", key="шаг", value="x", path="cli.py", line=2,
                        category="STEP", mechanism="cli")
        exotic = Fact(kind="STEP", key="шаг", value="x", path="x.py", line=2,
                      category="STEP", mechanism="smart_contract")
        steps = policies.steps(self._extraction(cli_step, exotic))
        self.assertEqual([f.path for f in steps], ["cli.py"])


class WriterFailFastTest(unittest.TestCase):
    """Пустой ответ модели — отказ, а не тихая подмена шаблоном."""

    def _client(self, tmp: Path, payload: dict):
        from pko.llm.client import ChatClient
        from pko.llm.registry import ModelSpec

        spec = ModelSpec(role="writer", base_url="https://stub.local/v1",
                         model="stub", api_key="x")
        client = ChatClient(spec=spec, cache_dir=tmp)
        client.__class__._request = lambda self, m, p, b: payload
        self.addCleanup(lambda: delattr(ChatClient, "_request")
                        if "_request" in ChatClient.__dict__ else None)
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

    def test_diff_may_repeat_an_id_removed_from_current_model(self):
        from pko.diff.engine import ModelDiff, ObjectDiff, REMOVED
        from pko.llm.registry import ModelSpec
        from pko.report.writer import write_diff_narrative

        model = PkoModel(meta={"version_label": "current"})
        model.add(PkoObject(id="BBB-002", kind="BBB", name="Новый блок"))
        diff = ModelDiff(
            left_label="v1",
            right_label="current",
            objects=[ObjectDiff(REMOVED, "BBB", "BBB-001", "Старый блок")],
        )
        spec = ModelSpec(role="writer", base_url="https://stub", model="stub", api_key="x")
        with patch(
            "pko.report.writer.ChatClient.complete",
            return_value="Объект BBB-001 удалён из системы.",
        ):
            written = write_diff_narrative(diff, model, spec)
        self.assertEqual(written.source, "llm")

    def test_guard_rejection_is_an_error_in_llm_mode(self):
        from pko.errors import LlmError
        from pko.llm.registry import ModelSpec
        from pko.report.writer import write_overview

        model = PkoModel(meta={"repo": "demo", "commit": "abc"})
        spec = ModelSpec(role="writer", base_url="https://stub", model="stub", api_key="x")
        with patch(
            "pko.report.writer.ChatClient.complete",
            return_value="Система использует выдуманный BBB-999.",
        ):
            with self.assertRaises(LlmError):
                write_overview(model, spec)


class AtomicOperationEvidenceTest(unittest.TestCase):
    """Импорт зависимости не является исполненной атомарной операцией."""

    def test_static_external_import_is_not_counted_as_a_call(self):
        from pko.assemble.candidates import build_candidates
        from pko.assemble.heuristic import build_model

        imported = Fact(
            kind="EXTERNAL",
            key="Реляционная БД через SQLAlchemy",
            value="sqlalchemy",
            path="backend/models.py",
            line=1,
            basis="импорт sqlalchemy",
            mechanism="orm",
        )
        extraction = Extraction(facts=[imported])
        model = build_model(
            extraction=extraction,
            candidates=build_candidates(extraction),
            meta={"repo": "demo", "commit": "abc"},
        )
        self.assertEqual(model.by_kind("AO"), [])


class MigrationOriginTest(unittest.TestCase):
    """Изменение данных в миграции остаётся нарушением, но названо отдельно."""

    def test_migration_paths_are_recognised(self):
        from pko.extractors.base import is_migration

        self.assertTrue(is_migration("backend/alembic/versions/6fb_default.py"))
        self.assertTrue(is_migration("app/migrations/0001_initial.py"))
        self.assertFalse(is_migration("app/services/orders.py"))

    def test_note_names_the_origin_without_changing_the_verdict(self):
        from pko.gate import policies

        migration = Fact(kind="SQL_WRITE", key="sql", value="INSERT", line=1,
                         path="backend/alembic/versions/a.py")
        runtime = Fact(kind="SQL_WRITE", key="sql", value="UPDATE", line=2,
                       path="app/services/orders.py")

        only_migrations = policies.split_by_origin([migration])
        self.assertEqual(policies.origin_note(*only_migrations), "все 1 — в миграциях схемы")

        mixed = policies.split_by_origin([migration, runtime])
        self.assertEqual(policies.origin_note(*mixed), "из них в миграциях схемы: 1")
        self.assertEqual([f.path for f in mixed[0]], ["app/services/orders.py"])


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


class EntrypointSelectionTest(unittest.TestCase):
    """Витрина показывает представителей ресурсов, а не первые по алфавиту."""

    def test_creation_wins_over_deletion_and_total_is_named(self):
        from pko.assemble.heuristic import _representative_entrypoints, _with_total

        keys = ["DELETE /chats/", "DELETE /chats/{id}/", "DELETE /files/{id}",
                "DELETE /nodes/{key}/", "GET /chats", "POST /chats/",
                "POST /create-processes/"]
        routes = [Fact(kind="ROUTE", key=k, value="", path="api.py", line=1) for k in keys]
        shown, total = _representative_entrypoints(routes, limit=8)
        values = _with_total([f.key for f in shown], total)

        self.assertEqual(total, len(keys))
        self.assertTrue(values[0].startswith("POST"), msg=f"порядок: {values}")
        self.assertNotEqual({v.split()[0] for v in values[:-1]}, {"DELETE"},
                            msg="раньше витрина состояла из одних удалений")
        # `POST /chats/` и `GET /chats` — один ресурс, представитель один.
        self.assertEqual(sum(1 for v in values if "/chats" in v and "{" not in v), 1)
        self.assertIn("всего входов: 7", values[-1])


class ObjectNotesTest(unittest.TestCase):
    """Пояснения к объектам пишет модель, но сторож проверяет их так же строго."""

    def _model(self):
        from pko.model.schema import PkoModel, PkoObject

        model = PkoModel(meta={"repo": "demo", "commit": "abc", "version_label": "current"})
        obj = PkoObject(id="BBB-001", kind="BBB", name="Приём заявок")
        obj.set("Контракт входа", ["POST /tasks"], "OBSERVED", [])
        model.add(obj)
        return model

    def _with_answer(self, answer: str):
        from pko.llm.client import ChatClient

        original = getattr(ChatClient, "complete")
        ChatClient.complete = lambda self, system, user, **kw: answer
        self.addCleanup(setattr, ChatClient, "complete", original)

    def test_notes_are_parsed_and_attached_by_id(self):
        from pko.llm.registry import ModelSpec
        from pko.report.writer import write_object_notes

        self._with_answer('{"BBB-001": "Блок принимает заявки и ставит их в работу."}')
        spec = ModelSpec(role="writer", base_url="https://stub/v1", model="m", api_key="x")
        notes, problems = write_object_notes(self._model(), spec)

        self.assertEqual(problems, [])
        self.assertIn("BBB-001", notes)
        self.assertIn("заявки", notes["BBB-001"])

    def test_invented_identifier_is_refused(self):
        """Та же защита, что и у обзора: модель не вводит объектов, которых нет."""
        from pko.llm.registry import ModelSpec
        from pko.report.writer import write_object_notes

        self._with_answer('{"BBB-001": "Работает вместе с BBB-777 и AO-404."}')
        spec = ModelSpec(role="writer", base_url="https://stub/v1", model="m", api_key="x")
        notes, problems = write_object_notes(self._model(), spec)

        self.assertEqual(notes, {})
        self.assertTrue(problems and "отброшены сторожем" in problems[0])

    def test_without_writer_there_are_no_notes(self):
        from pko.report.writer import write_object_notes

        self.assertEqual(write_object_notes(self._model(), None), ({}, []))


if __name__ == "__main__":
    unittest.main()
