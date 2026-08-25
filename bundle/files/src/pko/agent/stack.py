"""Определение стека репозитория: какие паки промпта подключать.

Один промпт на все случаи либо перегружен примерами чужих технологий, либо
пуст. Оба варианта плохи: первый тратит контекст и уводит агента искать SQL
там, где его нет, второй не подсказывает ничего.

Поэтому промпт собирается: нейтральное ядро плюс паки по обнаруженному стеку.
Определение детерминированное — по манифестам, расширениям файлов и уже
собранным `DEP`-фактам, — поэтому один и тот же коммит всегда даёт один и тот
же промпт, и трассы разных прогонов сравнимы.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pko.agent import verifiers
from pko.extractors.base import CODE_SUFFIXES, DATA_SUFFIXES, Tree, is_vendor
from pko.extractors.runner import Extraction, is_out_of_perimeter

# Пак → зависимости, по которым он включается.
_PACK_BY_DEP = {
    "web": ("fastapi", "flask", "django", "starlette", "aiohttp", "express", "koa", "nestjs"),
    "data": ("sqlalchemy", "psycopg", "psycopg2", "asyncpg", "pymongo", "redis",
             "boto3", "alembic", "pandas", "duckdb", "clickhouse-driver"),
    "agents": ("langgraph", "langchain", "openai", "anthropic", "llama-index", "autogen"),
    "frontend": ("react", "vue", "svelte", "next", "streamlit", "gradio", "@angular/core"),
    "jobs": ("celery", "apscheduler", "airflow", "click", "typer", "prefect"),
    "messaging": ("kafka-python", "aiokafka", "pika", "confluent-kafka", "boto3-sqs",
                  "nats-py", "redis-om"),
}

# Пак → расширения файлов, по которым он включается даже без манифеста.
_PACK_BY_EXT = {
    "web": (".http",),
}

# Паки вне периметра первой версии: подключаются только явным `--agent-packs`.
# Автоподключение `frontend` звало агента разбирать `.tsx`, которые
# статический разбор не видит, — находки по ним нечем было сверить, и они
# уходили в отчёт как непроверяемые.
MANUAL_ONLY_PACKS = frozenset({"frontend"})

# Механизм найденного факта → пак. Самый надёжный признак: манифест может
# умалчивать о библиотеке (в фикстуре объявлен только fastapi, а код ходит в
# LangGraph и SQLAlchemy), тогда как механизм выведен из самого кода.
_PACK_BY_MECHANISM = {
    "graph": "agents", "llm": "agents", "agent_tool": "agents",
    "sql": "data", "orm": "data", "nosql": "data",
    "fs": "data", "object_storage": "data", "state_store": "data",
    "http_server": "web", "http_client": "web", "webhook": "web",
    "queue": "messaging",
    "ui_event": "frontend",
    "cli": "jobs", "cron": "jobs",
}

# Расширения прикладного кода, который PKO статически не разбирает. Это не
# ошибка, а честно названная граница: такие файлы читает только агент, и
# покрытие обязано это показывать.
UNPARSED_CODE_EXT = tuple(s for s in CODE_SUFFIXES if s not in {".py", ".sql", ".sh"})

# Границы просмотра дерева при определении стека.
MARKER_FILE_LIMIT = 400
MARKER_CHAR_LIMIT = 60_000
MARKER_EXT = CODE_SUFFIXES + DATA_SUFFIXES


@dataclass
class StackProfile:
    """Что за система перед нами и чем это подтверждено."""

    packs: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    # Файлы прикладного кода, которых статический разбор не касался.
    unparsed_files: int = 0
    unparsed_languages: list[str] = field(default_factory=list)
    reasons: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "packs": self.packs,
            "languages": self.languages,
            "unparsed_files": self.unparsed_files,
            "unparsed_languages": self.unparsed_languages,
            "reasons": self.reasons,
        }

    def coverage_note(self) -> str:
        """Пометка о пробеле покрытия для отчёта; пустая строка, если пробела нет."""
        if not self.unparsed_files:
            return ""
        langs = ", ".join(self.unparsed_languages)
        return (
            f"Статический разбор не покрывает {self.unparsed_files} файл(ов) "
            f"прикладного кода ({langs}): наблюдения по ним получены только агентом "
            f"и не подтверждены разбором синтаксиса."
        )


def detect(tree: Tree, extraction: Extraction | None = None) -> StackProfile:
    """Определить стек по дереву коммита и найденным зависимостям."""
    files = [p for p in tree.files if not is_vendor(p)]
    deps = {
        str(f.key).lower()
        for f in (extraction.by_kind("DEP") if extraction else [])
    }

    packs: dict[str, list[str]] = {}
    for pack, markers in _PACK_BY_DEP.items():
        hits = sorted(d for d in deps if any(d == m or d.startswith(m) for m in markers))
        if hits:
            packs.setdefault(pack, []).extend(f"зависимость {h}" for h in hits[:3])

    extensions = {_ext(p) for p in files}
    for pack, suffixes in _PACK_BY_EXT.items():
        hits = sorted(s for s in suffixes if s in extensions)
        if hits:
            packs.setdefault(pack, []).extend(f"файлы {h}" for h in hits)

    for mechanism, pack in sorted(_PACK_BY_MECHANISM.items()):
        example = next(
            (f for f in (extraction.facts if extraction else [])
             if f.facets.mechanism == mechanism), None,
        )
        if example is not None:
            packs.setdefault(pack, []).append(
                f"механизм {mechanism} ({example.path}:{example.line})"
            )

    # Статический разбор видит только Python и знакомые библиотеки: команду
    # argparse или отправку в брокер он не покажет, и пак не включился бы там,
    # где он нужнее всего. Поэтому дерево дополнительно просматривается теми же
    # признаками, которыми потом проверяются находки агента, — отдельной
    # таблицы шаблонов для этого заводить не нужно.
    for mechanism, where in _scan_markers(tree, files).items():
        pack = _PACK_BY_MECHANISM.get(mechanism)
        if pack:
            packs.setdefault(pack, []).append(f"признак {mechanism} ({where})")

    unparsed = [
        p for p in files
        if _ext(p) in UNPARSED_CODE_EXT and not is_out_of_perimeter(p)
    ]
    return StackProfile(
        packs=sorted(name for name in packs if name not in MANUAL_ONLY_PACKS),
        languages=sorted({e for e in extensions if e}),
        unparsed_files=len(unparsed),
        unparsed_languages=sorted({_ext(p) for p in unparsed}),
        reasons={name: sorted(set(why)) for name, why in sorted(packs.items())},
    )


def _scan_markers(tree: Tree, files: list[str]) -> dict[str, str]:
    """Первое место, где встретился признак каждого механизма.

    Просмотр ограничен по числу файлов и по объёму: определение стека — это
    маршрутизация промпта, а не доказательство, и платить за него полным
    обходом большого репозитория незачем.
    """
    patterns = {
        mechanism: found
        for mechanism, found in verifiers.patterns_by_mechanism().items()
        if mechanism in _PACK_BY_MECHANISM
    }

    found: dict[str, str] = {}
    for path in files[:MARKER_FILE_LIMIT]:
        if _ext(path) not in MARKER_EXT:
            continue
        text = tree.read(path)
        if not text:
            continue
        text = text[:MARKER_CHAR_LIMIT]
        for mechanism, candidates in patterns.items():
            if mechanism in found:
                continue
            for pattern in candidates:
                match = pattern.search(text)
                if match:
                    line = text.count("\n", 0, match.start()) + 1
                    found[mechanism] = f"{path}:{line}"
                    break
        if len(found) == len(patterns):
            break
    return found


def _ext(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    return "." + base.rsplit(".", 1)[-1].lower() if "." in base else ""
