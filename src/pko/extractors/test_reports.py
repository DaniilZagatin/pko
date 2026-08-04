"""Тесты и готовые отчёты pytest.

PKO не запускает тесты анализируемого проекта. Наличие файла с тестами — это
факт «тест написан», но не доказательство «поведение проверено». Доказательством
считается только готовый JUnit XML: в нём есть имя сценария, ожидание и
фактический исход, чего требует облегчённая Evidence Model BASIC (§5.2.4).

Исход каждого теста сохраняется отдельно. Пропущенный тест доказательством не
является: в анализируемых проектах часть сценариев штатно скипается guard'ом
(например, тесты, требующие живого LLM), и отчёт, где всё пропущено, формально
не содержит падений — но и не подтверждает ничего.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from pko.extractors.base import Fact, Tree, is_vendor

NEGATIVE_MARKERS = ("negative", "invalid", "forbidden", "denied", "reject", "fail",
                    "error", "readonly", "read_only", "guard", "limit", "timeout")

# Исходы теста в отчёте JUnit.
PASSED = "passed"
FAILED = "failed"
SKIPPED = "skipped"


def extract(tree: Tree) -> list[Fact]:
    facts: list[Fact] = []
    for path in tree.files:
        if is_vendor(path):
            continue
        base = path.rsplit("/", 1)[-1]
        if not (base.startswith("test_") and path.endswith(".py")) and not base.endswith("_test.py"):
            continue
        text = tree.read(path) or ""
        negative = [
            line_no
            for line_no, line in enumerate(text.splitlines(), start=1)
            if line.lstrip().startswith(("def test", "async def test"))
            and any(m in line.lower() for m in NEGATIVE_MARKERS)
        ]
        facts.append(
            Fact(
                kind="TEST",
                key=path,
                value={"negative_cases": len(negative)},
                path=path,
                line=1,
                basis=f"файл тестов, негативных сценариев по имени: {len(negative)}",
            )
        )
    return facts


def load_junit(report_path: str | Path) -> list[Fact]:
    """Разобрать готовый JUnit XML, переданный флагом --junit."""
    p = Path(report_path)
    if not p.exists():
        return []
    try:
        root = ET.parse(p).getroot()
    except ET.ParseError:
        return []

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    facts: list[Fact] = []
    for suite in suites:
        cases = [_case_outcome(c) for c in suite.iter("testcase")]
        passed = [c["name"] for c in cases if c["outcome"] == PASSED]
        failed = [c["name"] for c in cases if c["outcome"] == FAILED]
        skipped = [c["name"] for c in cases if c["outcome"] == SKIPPED]
        facts.append(
            Fact(
                kind="TEST_REPORT",
                key=suite.get("name") or p.name,
                value={
                    "total": len(cases),
                    "failed": len(failed),
                    "skipped": len(skipped),
                    "passed": len(passed),
                    # Исход каждого теста хранится отдельно: пропущенный тест ничего
                    # не доказывает, а по одному имени его не отличить от прошедшего.
                    # Gate и связь «тест ↔ guardrail» должны видеть весь suite.
                    # Ограничивать этот компактный индекс можно только в renderer.
                    "cases": cases,
                    "passed_cases": passed,
                    "skipped_cases": skipped,
                },
                path=str(p),
                line=1,
                basis=(
                    f"отчёт pytest: {len(cases)} тестов, прошло {len(passed)}, "
                    f"провалено {len(failed)}, пропущено {len(skipped)}"
                ),
            )
        )
    return facts


def _case_outcome(case: ET.Element) -> dict[str, str]:
    """Исход одного теста. Пропуск — это не успех и не падение, а отсутствие проверки."""
    name = case.get("name", "")
    if case.find("failure") is not None or case.find("error") is not None:
        return {"name": name, "outcome": FAILED}
    if case.find("skipped") is not None:
        return {"name": name, "outcome": SKIPPED}
    return {"name": name, "outcome": PASSED}
