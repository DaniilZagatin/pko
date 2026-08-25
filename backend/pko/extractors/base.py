"""Общие типы экстракторов.

Факт — минимальная единица знания о системе. У него всегда есть путь и строка:
утверждение без ссылки на код в PKO не существует. Сам текст кода в факт не
попадает, только короткое основание своими словами.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from pko.git.repo import GitRepo
from pko.model import taxonomy
from pko.model.schema import Evidence

# Виды фактов, которые умеют производить экстракторы.
#
# Перечень остаётся как совместимый словарь прежних наблюдений: у каждого вида
# есть точное разложение на фасеты в `pko.model.taxonomy`. Универсальные
# категории (`ENTRYPOINT`, `EFFECT`, …) допустимы наравне с ними — так находка
# на незнакомом стеке получает место в модели, не требуя нового вида.
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
) + taxonomy.CATEGORIES


@dataclass(frozen=True)
class Fact:
    """Одно наблюдение, привязанное к строке кода.

    Фасеты необязательны: у прежних видов они выводятся из `kind` таблицей
    псевдонимов, поэтому существующие экстракторы размечать построчно не
    нужно. Явно их задают там, где один вид скрывает разные механизмы —
    например, `EXTERNAL` из `boto3` и из `requests` это хранилище и HTTP.
    """

    kind: str
    key: str
    value: Any
    path: str
    line: int | None = None
    basis: str = ""
    category: str = ""
    action: str = ""
    mechanism: str = ""
    # Может ли наблюдение участвовать в вердикте Gate. Факт экстрактора — да:
    # он получен разбором синтаксиса. Находка агента — только если её механизм
    # умеет проверять `pko.agent.verifiers`; иначе она попадёт в паспорт и в
    # пробелы, но решение о допуске изменить не сможет.
    gate_eligible: bool = True

    @property
    def facets(self) -> taxonomy.Facets:
        """Разложение наблюдения: явное, а где не задано — из вида."""
        base = taxonomy.facets_for(self.kind)
        return taxonomy.Facets(
            category=self.category or base.category,
            action=self.action or base.action,
            mechanism=self.mechanism or base.mechanism,
        )

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


# Расширения прикладного кода и конфигурации. Перечень один на весь проект:
# по нему определяется стек, считается непокрытая часть и проверяются пути в
# тексте отчёта. Разойдись эти три места — и на чужом стеке одно из них молча
# перестало бы работать.
CODE_SUFFIXES = (
    ".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".go", ".java",
    ".kt", ".rb", ".php", ".cs", ".rs", ".scala", ".swift", ".sql", ".sh",
)
DATA_SUFFIXES = (".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".md", ".xml")


# Каталоги миграций схемы. Их код исполняется один раз при обновлении схемы, а
# не в ходе автономного процесса, поэтому найденный там изменяющий SQL нужно
# уметь называть отдельно от изменений времени исполнения. Из анализа они не
# исключаются: миграция всё ещё меняет данные, и вердикт это учитывает.
MIGRATION_PARTS = ("/migrations/", "/alembic/versions/", "/db/migrate/", "/flyway/")


def is_migration(path: str) -> bool:
    p = "/" + path
    return any(part in p for part in MIGRATION_PARTS)


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


# Файлы, которые задают поведение системы, не будучи кодом: режим, лимиты,
# перечни разрешённого, состав инструментов. Стандарт требует привязывать
# решение к версии кода **и существенной конфигурации** (§8.0.1), поэтому такой
# файл обязан попадать в снимок реализации наравне с коммитом.
#
# `.json` здесь есть, а в `deps.CONFIG_SUFFIXES` — нет, и это не рассогласование:
# `deps` вычитывает из конфигурации имена переменных окружения построчно, что для
# JSON бессмысленно, тогда как политики (`pko.extractors.policy_specs`) читают
# JSON наравне с YAML. Без `.json` файл, задающий режим и лимиты, не попадал в
# снимок вовсе, а запись допуска утверждала привязку к конфигурации.
CONFIG_SUFFIXES = (".yaml", ".yml", ".json", ".toml", ".ini", ".cfg")


def is_config_path(path: str) -> bool:
    """Похож ли путь на файл конфигурации, а не на исходный код."""
    return path.lower().endswith(CONFIG_SUFFIXES)
