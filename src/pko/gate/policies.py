"""Политики Gate: какие механизмы отвечают за какое утверждение.

Проверки допуска задавали вопросы про технологии — «есть ли узлы LangGraph»,
«есть ли изменяющий SQL». Из-за этого проект на другом стеке проваливал
проверку не потому, что траектория не восстановлена, а потому, что она
восстановлена не тем механизмом.

Здесь вопрос отделён от механизма: политика говорит, какие наблюдения
отвечают на утверждение, а перечень механизмов задаётся явно и в одном месте.
Это же делает расширение видимым: добавить механизм к политике — отдельное
решение, меняющее вердикты, а не побочный эффект новой технологии.

Правило, общее для всех политик: вердикт опирается только на наблюдения с
`gate_eligible`. Находка агента, механизм которой никто не умеет проверять
структурно, в отчёт попадает, а в решение — нет.
"""

from __future__ import annotations

from pko.extractors.base import Fact, is_migration
from pko.extractors.runner import Extraction
from pko.model import taxonomy

# Механизмы, которыми доказывается восстановленная траектория (CHK-AP-001).
#
# Это расширение, а не сохранение прежнего смысла — и его надо читать именно
# так. Статический разбор порождает шаг только в механизме `graph`
# (`GRAPH_NODE`); `cli`, `queue`, `cron`, `http_server` и `agent_tool` могут
# прийти только от агента. Следствие: на стеке без графа исполнения проверка
# CHK-AP-001 теперь способна перейти из FAIL в PASS — там, где раньше
# траектория считалась невосстановимой. Это и есть цель универсализации, но
# цена ей — вердикт, зависящий от находок агента, поэтому каждый такой
# механизм обязан иметь структурную проверку в `pko.agent.verifiers`.
TRAJECTORY_MECHANISMS = frozenset({"graph", "http_server", "agent_tool", "cli", "queue", "cron"})

# Механизмы, которыми доказывается точка входа (CHK-AP-001).
#
# Тоже расширение: раньше точкой входа был только HTTP-эндпоинт (`ROUTE`).
# Теперь ими считаются экранное событие, команда, расписание, очередь и
# входящий вызов. Ограничение по механизму обязательно: категория сама по
# себе доказательством не является, иначе достаточно было бы её объявить,
# чтобы наполнить проверку.
ENTRYPOINT_MECHANISMS = frozenset({"http_server", "ui_event", "cli", "cron", "queue", "webhook"})

# Механизмы доступа к данным для CHK-GRD-001 — те же, что в
# `taxonomy.GATE_DATA_MECHANISMS`: SQL и ORM. Файлы, объектные хранилища и
# документные базы в вердикт не входят; найденные там изменения попадают в
# пробелы отчёта, а не остаются незамеченными.
DATA_MECHANISMS = taxonomy.GATE_DATA_MECHANISMS


def split_by_origin(facts: list[Fact]) -> tuple[list[Fact], list[Fact]]:
    """Разделить наблюдения на миграционные и относящиеся к исполнению.

    Вердикт от этого не меняется: изменение данных остаётся изменением. Но
    читателю карточки существенно, все ли пять мест лежат в миграциях —
    иначе основание «найден SQL, изменяющий данные» звучит так, будто процесс
    пишет в базу на ходу.
    """
    migrations = [f for f in facts if is_migration(f.path)]
    runtime = [f for f in facts if not is_migration(f.path)]
    return runtime, migrations


def origin_note(runtime: list[Fact], migrations: list[Fact]) -> str:
    """Короткая пометка о происхождении изменений; пустая, если делить нечего."""
    if not migrations:
        return ""
    if not runtime:
        return f"все {len(migrations)} — в миграциях схемы"
    return f"из них в миграциях схемы: {len(migrations)}"


def unguarded_writes(extraction: Extraction) -> list[Fact]:
    """Изменения данных, которых проверка «только чтение» не касается."""
    return [
        f for f in admissible(extraction.select(taxonomy.is_effect, action="write"))
        if f.facets.mechanism in taxonomy.UNGUARDED_DATA_MECHANISMS
    ]


def admissible(facts: list[Fact]) -> list[Fact]:
    """Оставить наблюдения, на которые вердикту разрешено опираться."""
    return [f for f in facts if f.gate_eligible]


def entrypoints(extraction: Extraction) -> list[Fact]:
    return [
        f for f in admissible(extraction.select(taxonomy.is_entrypoint))
        if f.facets.mechanism in ENTRYPOINT_MECHANISMS
    ]


def steps(extraction: Extraction) -> list[Fact]:
    """Шаги траектории — только в механизмах, которыми она доказывается."""
    return [
        f for f in admissible(extraction.select(taxonomy.is_step))
        if f.facets.mechanism in TRAJECTORY_MECHANISMS
    ]


def transitions(extraction: Extraction) -> list[Fact]:
    return admissible(extraction.select(taxonomy.is_transition))


def data_reads(extraction: Extraction) -> list[Fact]:
    return admissible(extraction.select(
        lambda f: taxonomy.is_data_effect(f, DATA_MECHANISMS), action="read"))


def data_writes(extraction: Extraction) -> list[Fact]:
    return admissible(extraction.select(
        lambda f: taxonomy.is_mutating(f, DATA_MECHANISMS)))


def controls(extraction: Extraction) -> list[Fact]:
    return admissible(extraction.select(taxonomy.is_control, mechanism="limit"))


def unverifiable(extraction: Extraction) -> list[Fact]:
    """Наблюдения, исключённые из вердикта: попадают в пробелы отчёта."""
    return [f for f in extraction.facts if not f.gate_eligible]
