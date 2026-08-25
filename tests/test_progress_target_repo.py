"""Чтение целевого репозитория для пайплайна прогресса — тонкая обвязка над
уже проверенными git/extractors, поэтому проверяется только само подключение.
"""

import unittest

from fixture_support import ensure_fixture
from pko.git.repo import GitRepo
from pko.progress.target_repo import load_target


class LoadTargetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = GitRepo(ensure_fixture())

    def test_loads_pinned_commit_with_facts(self):
        target = load_target(self.repo, branch="master")
        self.assertEqual(target.branch, "master")
        self.assertTrue(target.sha)
        self.assertEqual(target.tree.sha, target.sha)
        self.assertGreater(len(target.tree.files), 0)
        self.assertGreater(len(target.extraction.facts), 0)

    def test_default_branch_used_when_not_specified(self):
        target = load_target(self.repo)
        self.assertTrue(target.branch)


if __name__ == "__main__":
    unittest.main()
