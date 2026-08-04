"""Запуск всех экстракторов и подсчёт покрытия анализа."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pko.extractors import deps as deps_ex
from pko.extractors import ownership as ownership_ex
from pko.extractors import prompts as prompts_ex
from pko.extractors import python_code
from pko.extractors import test_reports as tests_ex
from pko.extractors.base import Fact, Tree, is_vendor
from pko.model.schema import Coverage

# Что PKO умеет разбирать в первой версии.
ANALYZED_GLOBS = ["*.py", "pyproject.toml", "requirements*.txt", "package.json",
                  "*.yaml", "*.yml", "*.toml", "*.ini", "*.cfg", ".env*", "CODEOWNERS"]


@dataclass
class Extraction:
    """Результат разбора одной версии."""

    facts: list[Fact] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)
    parse_failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[Fact]:
        return [f for f in self.facts if f.kind == kind]


def extract_all(tree: Tree, junit_path: str | Path | None = None) -> Extraction:
    """Собрать факты по снимку репозитория."""
    facts, parsed, failed = python_code.extract(tree)
    facts.extend(deps_ex.extract(tree))
    facts.extend(prompts_ex.extract(tree))
    facts.extend(ownership_ex.extract(tree))
    facts.extend(tests_ex.extract(tree))

    notes: list[str] = []
    if junit_path:
        junit_facts = tests_ex.load_junit(junit_path)
        if junit_facts:
            facts.extend(junit_facts)
        else:
            notes.append(f"Отчёт о тестах не прочитан: {junit_path}")

    coverage = _coverage(tree, parsed)
    if failed:
        notes.append(f"Не удалось разобрать файлов Python: {len(failed)}")
    skipped_kinds = _skipped_summary(tree, parsed)
    if skipped_kinds:
        notes.append("Не анализировались: " + ", ".join(skipped_kinds))

    return Extraction(facts=facts, coverage=coverage, parse_failures=failed, notes=notes)


def _coverage(tree: Tree, parsed: list[str]) -> Coverage:
    meaningful = [p for p in tree.files if not is_vendor(p)]
    analyzed = set(parsed)
    for p in meaningful:
        base = p.rsplit("/", 1)[-1].lower()
        if (
            base in {"pyproject.toml", "package.json", "codeowners"}
            or base.startswith("requirements")
            or base.startswith(".env")
            or p.lower().endswith((".yaml", ".yml", ".toml", ".ini", ".cfg"))
        ):
            analyzed.add(p)

    skipped = sorted({_ext_group(p) for p in meaningful if p not in analyzed})
    return Coverage(
        files_total=len(meaningful),
        files_analyzed=len(analyzed),
        analyzed_globs=list(ANALYZED_GLOBS),
        skipped_globs=skipped[:20],
    )


def _skipped_summary(tree: Tree, parsed: list[str]) -> list[str]:
    groups: dict[str, int] = {}
    for p in tree.files:
        if is_vendor(p) or p in set(parsed):
            continue
        ext = _ext_group(p)
        if ext in {"*.py", "*.toml", "*.yaml", "*.yml", "*.json"}:
            continue
        groups[ext] = groups.get(ext, 0) + 1
    top = sorted(groups.items(), key=lambda kv: -kv[1])[:5]
    return [f"{ext} ({n})" for ext, n in top if n >= 3]


def _ext_group(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    if "." not in base:
        return "без расширения"
    return "*." + base.rsplit(".", 1)[-1].lower()
