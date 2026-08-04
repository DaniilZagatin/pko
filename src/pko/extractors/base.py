"""Общие типы экстракторов.

Факт — минимальная единица знания о системе. У него всегда есть путь и строка:
утверждение без ссылки на код в PKO не существует. Сам текст кода в факт не
попадает, только короткое основание своими словами.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from pko.git.repo import GitRepo
from pko.model.schema import Evidence

# Виды фактов, которые умеют производить экстракторы.
FACT_KINDS = (
    "DEP",          # зависимость и её версия
    "ROUTE",        # HTTP-эндпоинт
    "GRAPH",        # граф исполнения найден
    "GRAPH_NODE",   # узел графа
    "GRAPH_EDGE",   # переход графа
    "TOOL",         # инструмент агента
    "SETTING",      # параметр конфигурации
    "LIMIT",        # числовое ограничение (таймаут, лимит строк, число попыток)
    "ALLOWLIST",    # явный перечень разрешённого
    "SQL_READ",
    "SQL_WRITE",
    "EXTERNAL",     # внешняя система
    "LLM_CALL",     # обращение к языковой модели
    "PROMPT",       # файл промптов
    "MODULE",       # прикладной модуль (кандидат в BBB)
    "OWNER",        # техвладелец из CODEOWNERS
    "TEST",         # тест или готовый отчёт о тестах
    "TEST_REPORT",
)


@dataclass(frozen=True)
class Fact:
    """Одно наблюдение, привязанное к строке кода."""

    kind: str
    key: str
    value: Any
    path: str
    line: int | None = None
    basis: str = ""

    def evidence(self, commit: str) -> Evidence:
        return Evidence(commit=commit, path=self.path, line=self.line, basis=self.basis)


@dataclass
class Tree:
    """Снимок репозитория на конкретном коммите."""

    repo: GitRepo
    sha: str
    files: list[str] = field(default_factory=list)
    _text_cache: dict[str, str | None] = field(default_factory=dict, repr=False)

    @staticmethod
    def at(repo: GitRepo, sha: str) -> "Tree":
        return Tree(repo=repo, sha=sha, files=repo.files(sha))

    def read(self, path: str) -> str | None:
        if path not in self._text_cache:
            self._text_cache[path] = self.repo.read_text(self.sha, path)
        return self._text_cache[path]

    def match(self, suffixes: Iterable[str] = (), names: Iterable[str] = ()) -> list[str]:
        suffixes = tuple(suffixes)
        names = {n.lower() for n in names}
        out: list[str] = []
        for p in self.files:
            base = p.rsplit("/", 1)[-1].lower()
            if suffixes and p.lower().endswith(suffixes):
                out.append(p)
            elif names and base in names:
                out.append(p)
        return out


# Каталоги, которые не являются прикладным кодом анализируемой системы.
VENDOR_PARTS = (
    "/node_modules/",
    "/.venv/",
    "/venv/",
    "/site-packages/",
    "/migrations/versions/",
    "/.git/",
    "/dist/",
    "/build/",
    "/__pycache__/",
)


def is_vendor(path: str) -> bool:
    p = "/" + path
    return any(part in p for part in VENDOR_PARTS)
