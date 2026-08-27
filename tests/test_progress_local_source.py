"""ZIP/файлы как источник evidence — без git и вместе с git.

`LocalTree`/`CombinedTree` должны быть неотличимы от `Tree` для `ToolBox` —
это и есть смысл `extractors.base.FileTree`: агенту без разницы, что за
источником, если контракт (`.files`/`.read(path)`) один и тот же.
"""

import io
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from fixture_support import ensure_fixture
from pko.errors import PkoError
from pko.extractors.base import Tree
from pko.git.repo import GitRepo
from pko.progress.agent_tools import ToolBox
from pko.progress.local_source import (
    MAX_WORKSPACE_FILES,
    build_empty_workspace,
    build_target_repo_from_uploads,
    merge_with_uploads,
)
from pko.progress.target_repo import load_target


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buf.getvalue()


class BuildFromUploadsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = Path(self.tmp.name) / "workspace"

    def test_plain_files_are_indexed_and_readable(self):
        target = build_target_repo_from_uploads(
            [("model.py", b"print('hi')\n"), ("metrics.json", b'{"roc_auc": 0.88}')],
            self.dest,
        )
        self.assertIsNone(target.repo)
        self.assertEqual(target.sha, "")
        self.assertEqual(sorted(target.tree.files), ["metrics.json", "model.py"])
        self.assertEqual(target.tree.read("model.py"), "print('hi')\n")

    def test_uploaded_zip_is_extracted_not_stored_as_opaque_file(self):
        data = _zip_bytes({"src/app.py": b"x = 1\n", "README.md": b"# hi\n"})
        target = build_target_repo_from_uploads([("project.zip", data)], self.dest)
        self.assertEqual(sorted(target.tree.files), ["README.md", "src/app.py"])
        self.assertEqual(target.tree.read("src/app.py"), "x = 1\n")

    def test_zip_slip_is_rejected_entirely(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as archive:
            archive.writestr("../../etc/evil.txt", b"pwned")
        with self.assertRaises(PkoError):
            build_target_repo_from_uploads([("evil.zip", buf.getvalue())], self.dest)

    def test_traversal_in_plain_filename_is_rejected(self):
        with self.assertRaises(PkoError):
            build_target_repo_from_uploads([("../outside.py", b"x")], self.dest)

    def test_too_many_files_is_rejected(self):
        uploads = [(f"f{i}.txt", b"x") for i in range(MAX_WORKSPACE_FILES + 1)]
        with self.assertRaises(PkoError):
            build_target_repo_from_uploads(uploads, self.dest)

    def test_no_files_is_rejected(self):
        with self.assertRaises(PkoError):
            build_target_repo_from_uploads([], self.dest)

    def test_empty_workspace_is_valid_and_readable_by_tool_box(self):
        # Ни репозиторий, ни файлы не предоставлены — это не ошибка на этом
        # уровне (build_target_repo_from_uploads([]) выше — намеренно другой
        # случай: "выбран путь через файлы, но список пуст"). Пустой
        # workspace — законный вход для агента, см. web/analyses.py::_build_target.
        target = build_empty_workspace(self.dest)
        self.assertIsNone(target.repo)
        self.assertEqual(target.tree.files, [])
        tools = ToolBox(target.tree)
        listing = tools.call("list_files", {})
        self.assertIn("ничего не найдено", listing.content)

    def test_tool_box_reads_local_tree_the_same_way_as_git_tree(self):
        target = build_target_repo_from_uploads([("model.py", b"def f():\n    return 1\n")], self.dest)
        tools = ToolBox(target.tree)
        listing = tools.call("list_files", {"glob": "*.py"})
        self.assertIn("model.py", listing.content)
        content = tools.call("read_file", {"path": "model.py"})
        self.assertIn("return 1", content.content)


class MergeWithUploadsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = GitRepo(ensure_fixture())

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dest = Path(self.tmp.name) / "workspace"
        self.target = load_target(self.repo, "master")

    def test_merged_tree_has_both_git_and_uploaded_files(self):
        merged = merge_with_uploads(self.target, [("metrics.json", b'{"acc": 0.9}')], self.dest)
        self.assertIn("metrics.json", merged.tree.files)
        for path in self.target.tree.files:
            self.assertIn(path, merged.tree.files)
        self.assertEqual(merged.tree.read("metrics.json"), '{"acc": 0.9}')
        # git-происхождение сохраняется — это дополнение, не замена источника.
        self.assertEqual(merged.sha, self.target.sha)
        self.assertIs(merged.repo, self.target.repo)

    def test_uploaded_file_overrides_same_path_from_repo(self):
        [existing_path] = self.target.tree.files[:1] or [None]
        if existing_path is None:
            self.skipTest("фикстура пуста")
        merged = merge_with_uploads(self.target, [(existing_path, b"OVERRIDDEN")], self.dest)
        self.assertEqual(merged.tree.read(existing_path), "OVERRIDDEN")

    def test_no_uploads_returns_the_original_target_unchanged(self):
        merged = merge_with_uploads(self.target, [], self.dest)
        self.assertIs(merged, self.target)

    def test_extraction_is_recomputed_over_the_combined_tree(self):
        merged = merge_with_uploads(self.target, [("owners_extra/CODEOWNERS", b"* @team\n")], self.dest)
        merged_paths = {f.path for f in merged.extraction.facts}
        self.assertIn("owners_extra/CODEOWNERS", merged_paths)


if __name__ == "__main__":
    unittest.main()
