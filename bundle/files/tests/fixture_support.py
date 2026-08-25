"""Подготовка тестового репозитория.

Фикстура — настоящий git-репозиторий и потому не хранится в версионном контроле
(`.gitignore`). Раньше её нужно было создать отдельной командой, и на чистом
клоне набор тестов молча пропускался: гонять его в CI было нечем. Теперь любой
тест, которому нужна фикстура, создаёт её сам.
"""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
EXAMPLE_INTENT = PROJECT_ROOT / "examples" / "business_intent.yaml"
FIXTURE_REPO = TESTS_DIR / "fixtures" / "mini_repo"
FIXTURE_SCRIPT = TESTS_DIR / "make_fixture.sh"

# Синтетическая фикстура другого стека: интерфейс, командная строка, очередь и
# файлы. Нужна, чтобы слепота к чужой технологии проявлялась на прогоне.
MULTISTACK_REPO = TESTS_DIR / "fixtures" / "multistack_repo"
MULTISTACK_SCRIPT = TESTS_DIR / "make_fixture_multistack.sh"

JUNIT_OK = TESTS_DIR / "fixtures" / "junit_ok.xml"
JUNIT_ALL_SKIPPED = TESTS_DIR / "fixtures" / "junit_all_skipped.xml"


def ensure_multistack_fixture() -> Path:
    """Репозиторий другого стека; создаётся так же, как основная фикстура."""
    return _ensure(MULTISTACK_REPO, MULTISTACK_SCRIPT)


def ensure_fixture() -> Path:
    """Вернуть путь к фикстуре, создав её при необходимости.

    Пропуск допустим ровно в одном случае — скрипта нет в дереве. Если скрипт есть,
    но не отработал, прогон обязан покраснеть: иначе `unittest` завершится с нулём,
    а 27 сквозных тестов молча не выполнятся — та самая беззвучная потеря проверки,
    ради устранения которой фикстура и стала создаваться сама.
    """
    return _ensure(FIXTURE_REPO, FIXTURE_SCRIPT)


def _ensure(repo: Path, script: Path) -> Path:
    if (repo / ".git").exists():
        return repo
    if not script.exists():
        raise unittest.SkipTest(f"нет скрипта фикстуры: {script}")
    try:
        proc = subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            errors="replace",
        )
    except OSError as exc:  # нет bash — проверить нечем, это не повод зеленеть
        raise RuntimeError(f"не удалось запустить {script}: {exc}") from exc

    if proc.returncode != 0 or not (repo / ".git").exists():
        raise RuntimeError(
            f"не удалось создать фикстуру ({script}, код {proc.returncode}): "
            + (proc.stderr or proc.stdout).strip()[:500]
        )
    return repo
