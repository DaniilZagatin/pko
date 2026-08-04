"""Выбор версий: базовая версия считается по анализируемой ветке, а не по HEAD."""

import unittest

from fixture_support import ensure_fixture
from pko.git.repo import GitRepo
from pko.history.selector import select_versions


class BranchScopingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = GitRepo(ensure_fixture())

    def test_master_baseline(self):
        versions = select_versions(self.repo, "master", max_versions=2)
        self.assertEqual([v.label for v in versions], ["v1", "current"])
        self.assertEqual(versions[0].reason, "первый коммит с прикладным кодом")
        self.assertIn("backend/src/api/v1/router.py", self.repo.files(versions[0].sha))

    def test_baseline_of_other_branch_is_taken_from_that_branch(self):
        """Ветка alt: первый её коммит кода не содержит, второй — содержит.

        Без явной ревизии `git log` смотрел собственный HEAD репозитория, находил
        первый коммит master, не находил его в истории alt и откатывался к самому
        старому коммиту — то есть к версии вообще без кода.
        """
        versions = select_versions(self.repo, "alt", max_versions=2)
        baseline = versions[0]

        self.assertEqual(baseline.reason, "первый коммит с прикладным кодом")
        self.assertIn("alt/service.py", self.repo.files(baseline.sha))
        self.assertEqual(
            baseline.commit.subject, "Ветка alt: добавлен первый прикладной код"
        )

    def test_master_and_alt_baselines_differ(self):
        master = select_versions(self.repo, "master", max_versions=2)[0]
        alt = select_versions(self.repo, "alt", max_versions=2)[0]
        self.assertNotEqual(master.sha, alt.sha)

    def test_alt_baseline_is_not_the_code_free_commit(self):
        versions = select_versions(self.repo, "alt", max_versions=2)
        files = self.repo.files(versions[0].sha)
        self.assertTrue(
            any(f.endswith(".py") for f in files),
            msg="базовая версия не может быть коммитом без прикладного кода",
        )


if __name__ == "__main__":
    unittest.main()
