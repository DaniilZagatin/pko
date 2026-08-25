"""Контракт benchmark: clean checkout подготавливается, ошибки не зеленеют."""

from __future__ import annotations

import tempfile
import unittest
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bench import run_bench
from pko.errors import PkoError
from pko.llm.registry import ModelSpec


class BenchmarkContractTest(unittest.TestCase):
    def test_target_failure_makes_process_fail(self):
        spec = ModelSpec("scout", "https://llm.company.local/v1", "GLM-5.2", "x")
        args = SimpleNamespace(
            scout_base_url=spec.base_url,
            scout_model=spec.model,
            scout_api_key_env=None,
            scout_allowed_hosts="llm.company.local",
            targets="unused.yaml",
            agent_max_steps=1,
        )
        with tempfile.TemporaryDirectory() as raw, \
             patch.object(run_bench, "RUNS_DIR", Path(raw)), \
             patch.object(run_bench, "get_spec", return_value=spec), \
             patch.object(run_bench, "_load_targets", return_value=[{"name": "broken"}]), \
             patch.object(run_bench, "_run_target", side_effect=PkoError("сломано")):
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(run_bench._main(args), 1)

    def test_setup_script_creates_missing_fixture(self):
        tests_dir = run_bench.ROOT / "tests"
        with tempfile.TemporaryDirectory(prefix="bench-fixture-", dir=tests_dir) as raw:
            temp = Path(raw)
            repo = temp / "repo"
            script = temp / "make.sh"
            script.write_text(
                "#!/usr/bin/env bash\nset -e\ngit init -q \"$(dirname \"$0\")/repo\"\n",
                encoding="utf-8",
            )
            target = {"setup_script": str(script.relative_to(run_bench.ROOT))}
            run_bench._prepare_repo(target, repo)
            self.assertTrue((repo / ".git").is_dir())


if __name__ == "__main__":
    unittest.main()
