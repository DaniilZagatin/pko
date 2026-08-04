"""Контракт командной строки: имя репозитория, коды возврата, таймаут, устойчивость фикстуры."""

import subprocess
import contextlib
import io
import unittest
from pathlib import Path

from fixture_support import FIXTURE_SCRIPT, JUNIT_OK, ensure_fixture
from pko.cli import _build_parser, _repo_name
from pko.errors import GitError, PkoError
from pko.git.repo import GitRepo
from pko.history.selector import select_versions


class RepoNameTest(unittest.TestCase):
    """`--repo-path .` не должен давать пустое имя: ссылка на реализацию — смысл карточки."""

    def test_dot_resolves_to_directory_name(self):
        resolved = Path(".").expanduser().resolve()
        self.assertNotEqual(resolved.name, "")
        self.assertEqual(_repo_name(resolved), resolved.name)

    def test_bare_mirror_suffix_is_stripped(self):
        self.assertEqual(_repo_name(Path("/tmp/cache/ai-agent-deepresearch.git")),
                         "ai-agent-deepresearch")

    def test_never_empty(self):
        for path in (Path("/"), Path(".").resolve(), Path("/tmp/x/.git")):
            with self.subTest(path=str(path)):
                self.assertTrue(_repo_name(path))

    def test_implementation_ref_is_citable(self):
        """Ссылка вида `@<sha>` без имени репозитория не является точной ссылкой."""
        name = _repo_name(Path(".").resolve())
        ref = f"{name}@abcdef123456"
        self.assertFalse(ref.startswith("@"))


class GitTimeoutTest(unittest.TestCase):
    def test_timeout_becomes_pko_error(self):
        """Упереться в таймаут на большой истории — штатный исход, а не трассировка."""
        repo = GitRepo(ensure_fixture(), timeout=0)
        with self.assertRaises(GitError) as ctx:
            repo.run("log", "--format=%H")
        self.assertIn("не ответил", ctx.exception.message)
        self.assertIn("--network-timeout", ctx.exception.hint)


class MaxVersionsContractTest(unittest.TestCase):
    def test_argparse_rejects_zero_before_git(self):
        parser = _build_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                parser.parse_args(["history", "--repo-path", ".", "--max-versions", "0"])
        self.assertEqual(ctx.exception.code, 2)

    def test_selector_raises_readable_pko_error(self):
        repo = GitRepo(ensure_fixture())
        with self.assertRaises(PkoError) as ctx:
            select_versions(repo, "master", max_versions=0)
        self.assertIn("не меньше 1", ctx.exception.message)


class FixtureFailureTest(unittest.TestCase):
    """Сломанная фикстура обязана красить прогон, а не пропускать 27 тестов молча."""

    def test_broken_script_raises_instead_of_skipping(self):
        import fixture_support

        original = fixture_support.FIXTURE_REPO
        broken = original.parent / "no_such_fixture"
        fixture_support.FIXTURE_REPO = broken
        self.addCleanup(setattr, fixture_support, "FIXTURE_REPO", original)

        original_script = fixture_support.FIXTURE_SCRIPT
        fixture_support.FIXTURE_SCRIPT = original.parent / "broken_fixture.sh"
        self.addCleanup(setattr, fixture_support, "FIXTURE_SCRIPT", original_script)
        fixture_support.FIXTURE_SCRIPT.write_text("exit 7\n", encoding="utf-8")
        self.addCleanup(fixture_support.FIXTURE_SCRIPT.unlink, True)

        with self.assertRaises(RuntimeError) as ctx:
            fixture_support.ensure_fixture()
        self.assertNotIsInstance(ctx.exception, unittest.SkipTest)
        self.assertIn("код 7", str(ctx.exception))

    def test_missing_script_is_the_only_skip(self):
        import fixture_support

        original = fixture_support.FIXTURE_REPO
        fixture_support.FIXTURE_REPO = original.parent / "no_such_fixture"
        self.addCleanup(setattr, fixture_support, "FIXTURE_REPO", original)

        original_script = fixture_support.FIXTURE_SCRIPT
        fixture_support.FIXTURE_SCRIPT = original.parent / "absent.sh"
        self.addCleanup(setattr, fixture_support, "FIXTURE_SCRIPT", original_script)

        with self.assertRaises(unittest.SkipTest):
            fixture_support.ensure_fixture()

    def test_real_script_exists(self):
        self.assertTrue(FIXTURE_SCRIPT.exists())
        self.assertTrue(JUNIT_OK.exists())


class GateCardEscapingTest(unittest.TestCase):
    def test_pipe_in_owner_field_does_not_break_table(self):
        from pko.render.gate_card import _cell, _fmt

        self.assertEqual(_cell("Иванова | владелец"), "Иванова \\| владелец")
        self.assertEqual(_fmt(["a|b", "c"]), "a\\|b, c")
        self.assertEqual(_fmt(None), "")


if __name__ == "__main__":
    unittest.main()
