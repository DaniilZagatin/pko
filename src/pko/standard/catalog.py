"""Каталог требований стандарта и то, что из них PKO действительно проверяет.

Раньше степень покрытия стандарта существовала только в голове разработчика и
в README. Отчёт при этом печатал `машинный уровень FULL_RESOURCE_MODEL` — то
есть заявлял соответствие ресурсной модели §8.1–8.14, которой у PKO нет. Здесь
покрытие названо машинно и по пунктам: у каждого требования есть раздел
стандарта, профиль, источник доказательства и честное состояние.

Состояния намеренно четыре, а не два:

  * `CHECKED`      — проверяется детерминированно, результат влияет на Gate;
  * `PARTIAL`      — проверяется частично, границы названы в `limitation`;
  * `NOT_CHECKED`  — не проверяется, хотя проверить статически можно;
  * `NEEDS_RUNTIME`— проверить по коду нельзя: требуется исполняющий контур.

Разница между `NOT_CHECKED` и `NEEDS_RUNTIME` существенна: первое — работа,
которую можно сделать, второе — граница самого подхода. Смешивать их значит
обещать, что однажды статический анализ докажет наличие журнала исполнения.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CHECKED = "CHECKED"
PARTIAL = "PARTIAL"
NOT_CHECKED = "NOT_CHECKED"
NEEDS_RUNTIME = "NEEDS_RUNTIME"

STATES = (CHECKED, PARTIAL, NOT_CHECKED, NEEDS_RUNTIME)

BASIC = "BASIC"
FULL = "FULL"
BOTH = "BASIC+FULL"

# Области готовности: по ним же собирается оценка в `pko.model.readiness`.
AREA_OBJECTS = "Объектная модель"
AREA_MODES = "Режимы и полномочия"
AREA_POLICY = "Policy layer"
AREA_EFFECTS = "Внешние эффекты"
AREA_EVIDENCE = "Доказательства"
AREA_TRACE = "Журнал исполнения"
AREA_HANDOFF = "Fallback и handoff"
AREA_METRICS = "Метрики и EvalOps"
AREA_VERSIONING = "Версионирование"


@dataclass(frozen=True)
class Requirement:
    """Одно требование стандарта и состояние его проверки в PKO."""

    id: str
    section: str
    title: str
    profile: str
    area: str
    state: str
    source: str = ""
    limitation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "section": self.section,
            "title": self.title,
            "profile": self.profile,
            "area": self.area,
            "state": self.state,
            "source": self.source,
            "limitation": self.limitation,
        }


REQUIREMENTS: tuple[Requirement, ...] = (
    # --- объектная модель (§4) ---------------------------------------------
    Requirement(
        "OBJ-NEED", "4.1", "Паспорт потребности клиента", BOTH, AREA_OBJECTS, PARTIAL,
        source="business_intent.yaml + точки входа в коде",
        limitation="бизнес-смысл берётся из намерения владельца, из кода он не выводится",
    ),
    Requirement(
        "OBJ-JOURNEY", "4.2", "Паспорт клиентского пути", BOTH, AREA_OBJECTS, PARTIAL,
        source="business_intent.yaml",
        limitation="состояние «до» и критерии результата заявляются владельцем",
    ),
    Requirement(
        "OBJ-PROCESS", "4.3", "Паспорт автономного процесса", BOTH, AREA_OBJECTS, PARTIAL,
        source="граф исполнения, точки входа и шаги траектории; объявленные "
               "интерфейсы из OpenAPI и манифестов инструментов",
        limitation="правила выбора ветки из кода восстанавливаются не всегда",
    ),
    Requirement(
        "OBJ-BBB", "4.4", "Паспорт BBB", BOTH, AREA_OBJECTS, PARTIAL,
        source="группировка кандидатов по пакетам и находкам агента",
        limitation="контракт выхода статически не восстанавливается",
    ),
    Requirement(
        "OBJ-AO", "4.5", "Паспорт атомарной операции", BOTH, AREA_OBJECTS, PARTIAL,
        source="эффекты в коде: SQL, ORM, файлы, очереди, вызовы моделей",
        limitation="идемпотентность, компенсация и повтор в коде не размечены",
    ),
    Requirement(
        "OBJ-GRD", "4.6", "Паспорт guardrail", BOTH, AREA_OBJECTS, PARTIAL,
        source="числовые ограничения и перечни разрешённого в коде и в конфигурации, "
               "инвариант «только чтение»",
        limitation="ограничивающий эффект (LIMIT_MODE/LOG) из кода не восстановлен; "
                   "объявленное в конфигурации ограничение в вердикт не входит",
    ),
    # --- BASIC Gate (§5) ---------------------------------------------------
    Requirement(
        "GATE-CHECKS", "5.2", "Закрытый каталог применимых проверок", BASIC, AREA_OBJECTS,
        CHECKED, source="pko.gate.evaluate.CHECKS",
    ),
    Requirement(
        "GATE-NORMALIZE", "5.2.3.2", "PASS без доказательства не является результатом",
        BASIC, AREA_EVIDENCE, CHECKED, source="pko.gate.evaluate.evaluate_checks",
    ),
    Requirement(
        "GATE-DECIDE", "5.2.3.4", "Алгоритм решения Gate", BASIC, AREA_OBJECTS, CHECKED,
        source="pko.gate.decide.decide",
    ),
    Requirement(
        "GATE-PROFILE", "0.2.1", "Профилирование BASIC/FULL и блокер запуска", BOTH,
        AREA_OBJECTS, CHECKED, source="pko.gate.profile.determine_profile",
    ),
    Requirement(
        "GATE-SNAPSHOT", "8.0.1", "Точная версия реализации в записи допуска", BASIC,
        AREA_VERSIONING, CHECKED, source="коммит и ветка анализируемого репозитория",
    ),
    Requirement(
        "GATE-SCOPE", "8.0.1", "Зафиксированные scope и запрещённые эффекты", BASIC,
        AREA_MODES, PARTIAL, source="business_intent.yaml, раздел «Разрешённый scope» карточки",
        limitation="соблюдение scope в исполнении не проверяется — только его объявление",
    ),
    Requirement(
        "GATE-VALIDITY", "8.0.1", "Срок действия решения и условия инвалидирования",
        BASIC, AREA_VERSIONING, CHECKED,
        source="решение привязано к коммиту: изменение кода обесценивает карточку",
    ),
    # --- доказательства (§5.2.4) -------------------------------------------
    Requirement(
        "EVID-MODEL", "5.2.4", "Evidence Model: ссылка на факт у каждой проверки", BOTH,
        AREA_EVIDENCE, PARTIAL, source="path:line@commit у каждого поля и проверки",
        limitation="неизменяемость доказательства обеспечивается только коммитом git",
    ),
    Requirement(
        "EVID-TESTS", "5.2", "Протокол критических тестов", BOTH, AREA_EVIDENCE, CHECKED,
        source="готовый JUnit XML; PKO не запускает тесты анализируемой системы",
    ),
    # --- промышленные требования (§6) --------------------------------------
    Requirement(
        "FULL-BRANCHES", "6.3", "Правила выбора веток исполнения", FULL, AREA_MODES,
        NOT_CHECKED, source="—",
        limitation="условные переходы графа читаются, но правило выбора не извлекается",
    ),
    Requirement(
        "FULL-MODES", "6.4", "Максимальный режим для веток и действий", FULL, AREA_MODES,
        NOT_CHECKED, source="—",
        limitation="объявленный режим из конфигурации виден в паспортах, но в вердикт не "
                   "входит: строка в YAML не доказывает, что режим соблюдается",
    ),
    Requirement(
        "FULL-POLICY", "6.6", "Guardrails как исполняемый policy layer", FULL, AREA_POLICY,
        NEEDS_RUNTIME, source="—",
        limitation="ограничения видны и в коде, и в конфигурации, но ни то ни другое не "
                   "доказывает, что они применяются перед действием",
    ),
    Requirement(
        "FULL-EFFECTS", "6.6", "Полномочия на внешний эффект", FULL, AREA_EFFECTS, PARTIAL,
        source="найденные эффекты по механизмам",
        limitation="кто именно уполномочен на эффект, из кода не следует",
    ),
    Requirement(
        "FULL-FALLBACK", "6.7", "Fallback и безопасная остановка", FULL, AREA_HANDOFF,
        NOT_CHECKED, source="—",
        limitation="обработка ошибок в коде есть, но её соответствие fallback не доказано",
    ),
    Requirement(
        "FULL-HANDOFF", "6.7", "Исполняемый handoff человеку", FULL, AREA_HANDOFF,
        NEEDS_RUNTIME, source="—",
    ),
    Requirement(
        "FULL-TRACE", "6.8", "Журнал контроля и восстановимость траектории", FULL,
        AREA_TRACE, NEEDS_RUNTIME, source="—",
        limitation="журнал появляется при исполнении; статический разбор его не заменяет",
    ),
    Requirement(
        "FULL-EVALOPS", "6.8", "EvalOps и разбор отклонений", FULL, AREA_METRICS,
        NEEDS_RUNTIME, source="—",
    ),
    Requirement(
        "FULL-VERSIONING", "6.9", "Цикл изменений и версионирование", FULL,
        AREA_VERSIONING, PARTIAL, source="сравнение версий по коммитам",
        limitation="версионируется код, а не решения, политики и доказательства",
    ),
    Requirement(
        "FULL-METRICS", "7", "Измерение эффективности", FULL, AREA_METRICS, NEEDS_RUNTIME,
        source="—",
    ),
    # --- машинный контракт (§8) --------------------------------------------
    Requirement(
        "MACH-BASIC-RECORD", "8.0.1", "Контракт BASIC_RECORD", BASIC, AREA_OBJECTS, PARTIAL,
        source="basic_gate.json и gate_card_*.md — все поля §8.0.1",
        limitation="поля владельца (границы, среда, когорта, запрещённые эффекты) берутся "
                   "из business_intent.yaml; без него запись структурно полна, но пуста",
    ),
    # §8.0.2 — не FULL-требование: минимальную запись оставляет каждый запуск и
    # в BASIC тоже. Именно её отсутствие означает, что после допуска
    # проконтролировать исполнение нечем.
    Requirement(
        "MACH-BASIC-EXEC", "8.0.2", "Минимальная запись каждого исполнения", BASIC,
        AREA_TRACE, NEEDS_RUNTIME, source="—",
        limitation="запись появляется при запуске; статический разбор её не производит "
                   "и не заменяет",
    ),
    Requirement(
        "MACH-RESOURCE-MODEL", "8.1–8.14", "Ресурсная модель FULL_RESOURCE_MODEL", FULL,
        AREA_OBJECTS, NOT_CHECKED, source="—",
        limitation="нет resource envelope, ResourceRef, событий и инвариантов связности",
    ),
)


@dataclass
class Coverage:
    """Сводка покрытия: сколько требований в каком состоянии."""

    by_state: dict[str, int] = field(default_factory=dict)
    by_area: dict[str, dict[str, int]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"by_state": self.by_state, "by_area": self.by_area}


def requirements_for(profile: str) -> tuple[Requirement, ...]:
    """Требования, применимые к профилю. FULL включает и базовые."""
    if profile == FULL:
        return REQUIREMENTS
    return tuple(r for r in REQUIREMENTS if r.profile in (BASIC, BOTH))


def coverage(profile: str = FULL) -> Coverage:
    by_state: dict[str, int] = {state: 0 for state in STATES}
    by_area: dict[str, dict[str, int]] = {}
    for requirement in requirements_for(profile):
        by_state[requirement.state] += 1
        area = by_area.setdefault(requirement.area, {state: 0 for state in STATES})
        area[requirement.state] += 1
    return Coverage(by_state=by_state, by_area=by_area)


def blocking_for_full() -> tuple[Requirement, ...]:
    """Требования FULL, которые сейчас не выполняются.

    Именно они, а не процент готовности, отвечают на вопрос «чего не хватает
    для промышленного запуска».
    """
    return tuple(
        r for r in REQUIREMENTS
        if r.profile in (FULL, BOTH) and r.state in (NOT_CHECKED, NEEDS_RUNTIME)
    )


def to_dict(profile: str = FULL) -> dict[str, Any]:
    """Срез каталога под профиль.

    `omitted_for_profile` обязателен: без него отфильтрованный список для BASIC
    неотличим от полного каталога, и читатель решит, что требований всего
    столько.
    """
    applicable = requirements_for(profile)
    return {
        "schema": "pko-standard-coverage/0.2",
        "standard": "Автономный процесс v1.1",
        "profile": profile,
        "total_requirements": len(REQUIREMENTS),
        "applicable_requirements": len(applicable),
        "omitted_for_profile": [
            r.id for r in REQUIREMENTS if r not in applicable
        ],
        "coverage": coverage(profile).to_dict(),
        "requirements": [r.to_dict() for r in applicable],
    }
