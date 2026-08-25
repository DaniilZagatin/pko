"""Команды CLI целиком: что попадает в отчёт и к какой версии это относится.

Здесь проверяется одно свойство, общее для обеих команд: результат обязан
относиться ровно к тому коммиту, по которому он выпущен, и не зависеть от того,
какой командой его получили. Два пути, дающие разные карточки на одном коммите,
хуже одного неполного: читатель поверит тому файлу, который открыл.
"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fixture_support import ensure_fixture
from pko.cli import main

HEAD_INTENT = """confirmed_need_id: NEED-HEAD
business_owner: Иванова А.А.
target_state: Пользователь получил ответ
success_criteria: Ответ содержит ссылку
maturity: pilot
consequence: low
requested_mode: ASSIST
decision_boundary: END_TO_END_PROCESS
in_scope: Чтение данных и подготовка ответа
forbidden_effects: Изменение данных и внешние коммуникации
"""


def _run(argv: list[str]) -> tuple[int, str]:
    """Выполнить команду, не пуская её вывод в протокол тестов."""
    buffer = io.StringIO()
    stdout, sys.stdout = sys.stdout, buffer
    try:
        code = main(argv)
    finally:
        sys.stdout = stdout
    return code, buffer.getvalue()


class HistoricalIntentTest(unittest.TestCase):
    """`--intent` относится к головной версии и только к ней.

    Файл, переданный флагом, написан для сегодняшнего кода. Применённый ко
    всей истории, он выдавал допуск версии, которую никто не подтверждал: в
    исторической карточке стояло решение, а под ним — приписка «для этой версии
    подтверждение владельца не проверялось». Проверялось: именно оно и решило.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        intent = tmp / "business_intent.yaml"
        intent.write_text(HEAD_INTENT, encoding="utf-8")
        cls.out = tmp / "out"
        cls.code, _ = _run([
            "analyze", "--repo-path", str(ensure_fixture()), "--branch", "master",
            "--max-versions", "2", "--out", str(cls.out), "--intent", str(intent),
        ])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _card(self, label: str) -> str:
        return (self.out / f"gate_card_{label}.md").read_text(encoding="utf-8")

    def test_run_completes(self):
        self.assertIn(self.code, (0, 3, 4), msg="код возврата отражает вердикт")

    def test_head_card_uses_the_supplied_intent(self):
        self.assertIn("NEED-HEAD", self._card("current"))

    def test_historical_card_does_not_use_it(self):
        """Главное утверждение: чужое подтверждение не должно попасть в старую версию."""
        self.assertNotIn("NEED-HEAD", self._card("v1"))

    def test_historical_decision_is_not_issued_on_someone_elses_confirmation(self):
        """У фикстуры в первом коммите намерения нет — значит, решения быть не может."""
        card = self._card("v1")
        self.assertIn("`NO_DECISION`", card)

    def test_gap_says_what_actually_happened(self):
        """Пробел, расходящийся с решением в той же карточке, хуже отсутствия пробела."""
        card = self._card("v1")
        self.assertIn("--intent относится к версии", card)
        self.assertIn("не применялся", card)

    def test_head_card_has_no_such_gap(self):
        self.assertNotIn("--intent относится к версии", self._card("current"))


class GateCommandTest(unittest.TestCase):
    """`pko gate` обязан выпускать ту же запись, что и `pko analyze`."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        cls.repo = tmp / "repo"
        _make_repo(cls.repo)

        cls.gate_out = tmp / "gate"
        cls.analyze_out = tmp / "analyze"
        cls.gate_code, _ = _run(["gate", "--repo-path", str(cls.repo),
                                 "--branch", "main", "--out", str(cls.gate_out)])
        _run(["analyze", "--repo-path", str(cls.repo), "--branch", "main",
              "--max-versions", "1", "--out", str(cls.analyze_out)])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _cards(self) -> tuple[str, str]:
        return (
            (self.gate_out / "gate_card.md").read_text(encoding="utf-8"),
            (self.analyze_out / "gate_card_current.md").read_text(encoding="utf-8"),
        )

    def test_configuration_snapshot_matches_analyze(self):
        """`pko gate` печатал «конфигурация не найдена» там, где `analyze` печатал файл."""
        gate, analyze = self._cards()
        self.assertIn("`config/agent.json`", gate)
        self.assertIn("`config/agent.json`", analyze)

    def test_record_gaps_section_is_not_lost(self):
        """Раздел о незаполненных полях §8.0.1 пропадал из карточки отдельной команды."""
        gate, analyze = self._cards()
        self.assertIn("Чего не хватает в самой записи", gate)
        self.assertIn("Чего не хватает в самой записи", analyze)

    def test_both_commands_produce_the_same_card_apart_from_the_timestamp(self):
        gate, analyze = self._cards()
        self.assertEqual(_without_timestamp(gate), _without_timestamp(analyze))

    def test_machine_record_is_emitted(self):
        """Контракт §8.0.1 вычислен уже здесь; требовать ради него полный прогон незачем."""
        data = json.loads((self.gate_out / "basic_gate.json").read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], "pko-basic-gate/0.2")
        self.assertEqual(data["implementation"]["config_files"], ["config/agent.json"])
        self.assertTrue(data["record_gaps"])

    def test_exit_code_still_reflects_the_verdict(self):
        self.assertIn(self.gate_code, (0, 3, 4))


def _without_timestamp(card: str) -> list[str]:
    """Строки карточки без момента формирования: он у двух прогонов свой."""
    return [
        line for line in card.splitlines()
        if not line.startswith("> **Сформировано:**") and not line.startswith("| Когда |")
    ]


def _make_repo(path: Path) -> None:
    """Репозиторий с файлом политик: без него расхождение снимков не видно."""
    (path / "config").mkdir(parents=True)
    (path / "src").mkdir(parents=True)
    (path / "config" / "agent.json").write_text(
        json.dumps({"mode": "CONFIRM", "timeout": 30,
                    "allowed_hosts": ["internal.example"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (path / "src" / "app.py").write_text("def handle(query):\n    return query\n",
                                         encoding="utf-8")
    (path / "business_intent.yaml").write_text(HEAD_INTENT, encoding="utf-8")
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "PATH": "/usr/bin:/bin:/usr/local/bin"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True, env=env)


if __name__ == "__main__":
    unittest.main()
