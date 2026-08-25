"""Детерминированная сборка PKO-модели из фактов и кандидатов.

Работает без языковой модели: одинаковый коммит даёт одинаковую модель. Всё, что
нельзя вывести из кода — потребность, целевое состояние, границы автономности и
владелец результата — берётся только из business_intent.yaml (§4.0) и получает
origin `DECLARED`; при отсутствии файла остаётся `UNKNOWN`, а не выдумывается.
"""

from __future__ import annotations

import re
from typing import Any

from pko.assemble.candidates import Candidate
from pko.checks.test_link import READ_ONLY_KEY, confirming_cases
from pko.extractors.base import Fact
from pko.extractors.runner import Extraction
from pko.gate import policies
from pko.model import taxonomy
from pko.model.schema import Evidence, PkoModel, PkoObject

# Понятные названия для типовых пакетов. Незнакомый пакет получает нейтральное имя,
# а не выдуманный бизнес-смысл.
PACKAGE_TITLES = {
    "api": "Приём запросов пользователя",
    "router": "Маршрутизация запросов",
    "routers": "Маршрутизация запросов",
    "agent": "Агентское исполнение",
    "agents": "Агентское исполнение",
    "graph": "Оркестрация графа исполнения",
    "memory": "Работа с памятью",
    "db": "Доступ к реляционным данным",
    "db_tools": "Доступ к реляционным данным",
    "database": "Доступ к реляционным данным",
    "vector_db_tools": "Поиск по схемам данных",
    "vector": "Поиск по схемам данных",
    "search": "Поиск по схемам данных",
    "rbac": "Управление доступом",
    "auth": "Управление доступом",
    "config": "Конфигурация исполнения",
    "commands": "Команды оператора",
    "dreaming": "Фоновая обработка памяти",
    "presentations": "Подготовка материалов",
    "vision_recognition": "Распознавание изображений",
    "file_parsers": "Разбор загруженных файлов",
    "models": "Модели данных",
    "scripts": "Служебные операции",
    "services": "Сервисный слой",
    "tools": "Инструменты агента",
}

# Пакеты, которые не являются переиспользуемым бизнес-блоком.
SKIP_PACKAGES = {"tests", "test", "migrations", "alembic", "scripts", "models", "__pycache__"}

_MODE_HINTS = {
    "confirm": ("confirm", "approval", "подтвержд"),
    "auto": ("autonomous", "auto_run", "auto_execute"),
}


def build_model(
    extraction: Extraction,
    candidates: list[Candidate],
    meta: dict[str, Any],
    intent: dict[str, Any] | None = None,
    bbb_groups: dict[str, list[str]] | None = None,
    process_trajectory: list[str] | None = None,
    guardrail_invariants: list[dict[str, Any]] | None = None,
) -> PkoModel:
    """Собрать модель одной версии.

    `bbb_groups` — необязательная группировка от сборщика (GLM): {название: [id кандидатов]}.
    Если её нет, блоки собираются по пакетам, и результат остаётся воспроизводимым.
    """
    commit = meta.get("commit", "")
    slug = _slug(meta.get("repo", "system"))
    model = PkoModel(meta=dict(meta), coverage=extraction.coverage, facts_count=len(extraction.facts))
    intent = intent or {}

    need = _build_need(model, slug, intent, extraction, commit)
    journey = _build_journey(model, slug, intent, need, commit)
    bbbs = _build_bbbs(model, candidates, extraction, commit, bbb_groups)
    process = _build_process(
        model, slug, journey, bbbs, extraction, intent, commit, process_trajectory
    )
    _build_operations(model, bbbs, extraction, commit)
    guardrails = _build_guardrails(
        model, candidates, extraction, commit, guardrail_invariants, bbbs
    )

    process.links["bbb"] = [b.id for b in bbbs]
    process.links["guardrail"] = [g.id for g in guardrails]

    model.gaps.extend(_gaps(model, extraction, intent))
    return model


# --- потребность и путь ----------------------------------------------------
def _build_need(
    model: PkoModel, slug: str, intent: dict[str, Any], extraction: Extraction, commit: str
) -> PkoObject:
    obj = PkoObject(id=f"NEED-{slug}-001", kind="NEED", name=intent.get("need_name")
                    or "Потребность не подтверждена владельцем")
    declared = bool(intent)

    obj.set("Бизнес-смысл", intent.get("business_meaning"),
            "DECLARED" if intent.get("business_meaning") else "UNKNOWN",
            _intent_evidence(intent, "business_meaning"))
    obj.set("Подтверждённая потребность", intent.get("confirmed_need_id"),
            "DECLARED" if intent.get("confirmed_need_id") else "UNKNOWN",
            _intent_evidence(intent, "confirmed_need_id"))
    obj.set("Клиент", intent.get("client"),
            "DECLARED" if intent.get("client") else "UNKNOWN",
            _intent_evidence(intent, "client"))
    obj.set("Владелец результата", intent.get("business_owner"),
            "DECLARED" if intent.get("business_owner") else "UNKNOWN",
            _intent_evidence(intent, "business_owner"))

    routes = extraction.select(taxonomy.is_entrypoint)
    shown, total = _representative_entrypoints(routes, limit=8)
    obj.set(
        "Признаки распознавания",
        _with_total([f.key for f in shown], total) or None,
        "OBSERVED" if routes else "UNKNOWN",
        [f.evidence(commit) for f in shown[:3]],
    )
    if not declared:
        obj.set("Статус", "черновик: business_intent.yaml не найден", "INFERRED", [])
    return model.add(obj)


def _build_journey(
    model: PkoModel, slug: str, intent: dict[str, Any], need: PkoObject, commit: str
) -> PkoObject:
    obj = PkoObject(
        id=f"CP-{slug}-001",
        kind="JOURNEY",
        name=intent.get("journey_purpose") or "Клиентский путь не подтверждён владельцем",
    )
    obj.links["need"] = [need.id]
    obj.set("Потребность", need.id, "DECLARED" if intent else "INFERRED", [])
    obj.set("Бизнес-смысл", intent.get("journey_purpose"),
            "DECLARED" if intent.get("journey_purpose") else "UNKNOWN",
            _intent_evidence(intent, "journey_purpose"))
    obj.set("Состояние «до»", intent.get("initial_state"),
            "DECLARED" if intent.get("initial_state") else "UNKNOWN",
            _intent_evidence(intent, "initial_state"))
    obj.set("Целевое состояние", intent.get("target_state"),
            "DECLARED" if intent.get("target_state") else "UNKNOWN",
            _intent_evidence(intent, "target_state"))
    obj.set("Критерии результата", intent.get("success_criteria"),
            "DECLARED" if intent.get("success_criteria") else "UNKNOWN",
            _intent_evidence(intent, "success_criteria"))
    obj.set("Владелец", intent.get("business_owner"),
            "DECLARED" if intent.get("business_owner") else "UNKNOWN",
            _intent_evidence(intent, "business_owner"))
    return model.add(obj)


# --- автономный процесс ----------------------------------------------------
def _build_process(
    model: PkoModel,
    slug: str,
    journey: PkoObject,
    bbbs: list[PkoObject],
    extraction: Extraction,
    intent: dict[str, Any],
    commit: str,
    trajectory: list[str] | None = None,
) -> PkoObject:
    # Шаг, переход и точка входа берутся по смыслу: узел LangGraph, команда
    # CLI и обработчик очереди отвечают на один и тот же вопрос о траектории.
    nodes = extraction.select(taxonomy.is_step)
    edges = extraction.select(taxonomy.is_transition)
    routes = extraction.select(taxonomy.is_entrypoint)
    graph = extraction.by_kind("GRAPH")

    obj = PkoObject(id=f"AP-CP-{slug}-001", kind="PROCESS",
                    name="Автономный процесс клиентского пути")
    obj.links["journey"] = [journey.id]

    obj.set("Связанный клиентский путь", journey.id, "INFERRED", [])
    entry_shown, entry_total = _representative_entrypoints(routes, limit=10)
    obj.set(
        "Условия запуска",
        _with_total([f.key for f in entry_shown], entry_total) or None,
        "OBSERVED" if routes else "UNKNOWN",
        [f.evidence(commit) for f in entry_shown[:3]],
    )
    if nodes or graph:
        obj.set(
            "Правила сборки траектории",
            [f.key for f in sorted(nodes, key=lambda x: x.key)] or "граф объявлен, узлы не распознаны",
            "OBSERVED",
            [f.evidence(commit) for f in (nodes or graph)[:3]],
        )
        obj.set(
            "Переходы",
            [f.key for f in sorted(edges, key=lambda x: x.key)][:20] or None,
            "OBSERVED" if edges else "UNKNOWN",
            [f.evidence(commit) for f in edges[:3]],
        )
    elif trajectory:
        # Восстановлено агентом, а не найдено фактом: это интерпретация кода,
        # поэтому происхождение INFERRED, и в паспорте это видно бейджем.
        obj.set("Правила сборки траектории", list(trajectory), "INFERRED", [])
    else:
        obj.set("Правила сборки траектории", None, "UNKNOWN", [])

    # Перечень блоков выведен из модели, а не найден в коде отдельным фактом.
    obj.set("Допустимые BBB", [b.id for b in bbbs] or None,
            "INFERRED" if bbbs else "UNKNOWN", [])
    obj.set("Режим исполнения", intent.get("requested_mode"),
            "DECLARED" if intent.get("requested_mode") else "UNKNOWN",
            _intent_evidence(intent, "requested_mode"))
    obj.set("Владелец", intent.get("business_owner"),
            "DECLARED" if intent.get("business_owner") else "UNKNOWN",
            _intent_evidence(intent, "business_owner"))
    return model.add(obj)


# --- BBB и атомарные операции ---------------------------------------------
def _build_bbbs(
    model: PkoModel,
    candidates: list[Candidate],
    extraction: Extraction,
    commit: str,
    bbb_groups: dict[str, list[str]] | None = None,
) -> list[PkoObject]:
    groups = _group_candidates(candidates, bbb_groups)
    # Пакет, в котором есть внешний эффект (БД, хранилище, вызов модели), — это
    # бизнес-блок, даже если в нём нет ни эндпоинта, ни узла графа.
    effect_packages = {
        _package_of(f.path.rsplit("/", 1)[0])
        for f in extraction.select(taxonomy.is_effect)
    }

    out: list[PkoObject] = []
    for i, (group_name, cands) in enumerate(sorted(groups.items()), start=1):
        # «Значимый» — это точка входа, шаг или инструмент: то, что несёт
        # собственное поведение, в отличие от модуля-каталога.
        meaningful = [c for c in cands if _is_meaningful(c)]
        modules = [c for c in cands if _mechanism_of(c) == "package"]
        has_effect = any(_package_of(c.group) in effect_packages for c in cands)
        if not meaningful and not has_effect and _module_weight(modules) < 2:
            continue

        pkg = _common_package(cands)
        obj = PkoObject(
            id=f"BBB-{len(out) + 1:03d}",
            kind="BBB",
            name=group_name if bbb_groups else _package_title(pkg),
            candidates=[c.id for c in sorted(cands, key=lambda x: x.id)],
        )
        # Группа от сборщика может собрать кандидатов из разных каталогов.
        # Раньше блок помнил только общий пакет, и операции из второго каталога
        # оставались без владельца — они исчезали из отчёта. Теперь блок
        # помнит все свои пакеты; первый — основной, по нему идёт сравнение.
        obj.links["package"] = [pkg] + [
            other for other in _all_packages(cands) if other != pkg
        ]
        # Пакет может быть общим у двух блоков: сборщик забрал часть кандидатов
        # каталога в свою группу, остаток ушёл в блок по пакету. Точный ответ
        # даёт не пакет, а путь самого кандидата, поэтому владение решается
        # по нему; пакет остаётся запасным вариантом.
        obj.links["owned_paths"] = sorted({c.group for c in cands if c.group})
        # Номер BBB-NNN позиционный: новый пакет сдвигает нумерацию всех следующих
        # блоков. Сравнение версий опирается на пакет, а не на номер.
        obj.links["stable_key"] = [pkg or obj.name]

        routes = [c for c in cands if _category_of(c) == taxonomy.ENTRYPOINT]
        tools = [c for c in cands if _mechanism_of(c) == "agent_tool"]
        nodes = [c for c in cands if _category_of(c) == taxonomy.STEP]

        obj.set("Бизнес-смысл", obj.name, "INFERRED",
                [Evidence(commit, pkg, 1, "восстановлено по составу пакета")])
        obj.set("Контракт входа",
                [c.name for c in routes[:6]] or "внутренний вызов",
                "OBSERVED" if routes else "INFERRED",
                [c.facts[0].evidence(commit) for c in routes[:3]])
        obj.set("Контракт выхода", "не восстановлен статически", "UNKNOWN", [])
        obj.set("Способы исполнения",
                _exec_kinds(routes, tools, nodes) or "прямой вызов кода",
                "OBSERVED", [c.facts[0].evidence(commit) for c in cands[:3]])
        obj.set("Инструменты", [c.name for c in tools[:10]] or None,
                "OBSERVED" if tools else "UNKNOWN",
                [c.facts[0].evidence(commit) for c in tools[:3]])
        # Список кандидатов выписывается в паспорт намеренно: только так человек может
        # проверить саму сборку блока, а не поверить ей на слово.
        obj.set("Собран из кандидатов", [c.id for c in cands][:20], "OBSERVED",
                [c.facts[0].evidence(commit) for c in cands[:3]])
        obj.set("Владелец", None, "UNKNOWN", [])
        out.append(model.add(obj))
    return out


def _build_operations(
    model: PkoModel, bbbs: list[PkoObject], extraction: Extraction, commit: str
) -> list[PkoObject]:
    """Атомарная операция — минимальный шаг с проверяемым внешним эффектом (§4.5).

    Ключ группировки — смысл операции: целевая система, механизм и действие.
    Раньше в ключ входил ещё и пакет, и одно и то же обращение к базе из шести
    каталогов давало шесть одинаковых по названию операций — управленческий
    слой превращался в перечень мест вызова. Чтение и запись не сливаются
    никогда: это разные операции по последствиям, и вердикт различает их.
    """
    grouped: dict[tuple[str, str, str], list[Fact]] = {}
    owners: dict[tuple[str, str, str], list[str]] = {}
    for fact in extraction.select(taxonomy.is_effect):
        # Импорт библиотеки доказывает зависимость и доступность механизма, но
        # не выполнение операции. Раньше 89 `import sqlalchemy` становились
        # AO с полем «89 мест вызова». Настоящие вызовы остаются: статический
        # SQL/LLM и проверенные агентом EFFECT-наблюдения имеют иной источник.
        if fact.kind == "EXTERNAL" and fact.basis.startswith("импорт "):
            continue
        owner = _owning_bbb(bbbs, fact.path)
        facets = fact.facets
        label = _effect_label(fact)
        key = (label, facets.mechanism, facets.action)
        grouped.setdefault(key, []).append(fact)
        if owner is not None and owner.id not in owners.setdefault(key, []):
            owners[key].append(owner.id)

    out: list[PkoObject] = []
    for i, (key, facts) in enumerate(sorted(grouped.items()), start=1):
        label, mechanism, action = key
        bbb_ids = owners.get(key, [])
        obj = PkoObject(id=f"AO-{i:03d}", kind="AO", name=label)
        # Номер AO-NNN позиционный, поэтому устойчивая идентичность операции —
        # это её смысл: что происходит, чем и в какую сторону. От каталога она
        # больше не зависит, и переезд кода не выглядит новой операцией.
        obj.links["stable_key"] = [f"{mechanism}|{action}|{label}"]
        if bbb_ids:
            obj.links["bbb"] = bbb_ids
        packages = sorted({_package_of(f.path.rsplit("/", 1)[0]) for f in facts})
        # Принадлежность блоку выведена по пакету, а не найдена отдельным фактом.
        obj.set("Связанный BBB", ", ".join(bbb_ids) or "не определён",
                "INFERRED" if bbb_ids else "UNKNOWN", [])
        obj.set("Проверяемый эффект", label, "OBSERVED", [f.evidence(commit) for f in facts[:5]])
        # Механизм назван отдельно: одна и та же операция «изменение данных»
        # через SQL и через ORM проверяется по-разному, и читателю нужно
        # видеть, чем именно она сделана.
        obj.set("Механизм", mechanism or None,
                "OBSERVED" if mechanism else "UNKNOWN",
                [f.evidence(commit) for f in facts[:3]] if mechanism else [])
        # Слияние по смыслу не должно скрывать, где именно операция живёт:
        # состав компонентов остаётся в паспорте.
        obj.set("Компоненты", packages[:8], "OBSERVED",
                [f.evidence(commit) for f in facts[:3]])
        # Наблюдение вне вердикта видно в паспорте, а не только в пробелах.
        outside = [f for f in facts if not f.gate_eligible]
        if outside:
            obj.set("Участие в вердикте Gate",
                    "не участвует: наблюдение не подтверждено разбором реализации", "INFERRED",
                    [f.evidence(commit) for f in outside[:2]])
        obj.set("Количество мест вызова", len(facts), "OBSERVED",
                [f.evidence(commit) for f in facts[:5]])
        obj.set("Повтор и компенсация", "правило не найдено в коде", "UNKNOWN", [])
        obj.set("Владелец и аудит", None, "UNKNOWN", [])
        out.append(model.add(obj))
    return out


# --- guardrails ------------------------------------------------------------
def _build_guardrails(
    model: PkoModel,
    candidates: list[Candidate],
    extraction: Extraction,
    commit: str,
    invariants: list[dict[str, Any]] | None = None,
    bbbs: list[PkoObject] | None = None,
) -> list[PkoObject]:
    out: list[PkoObject] = []
    # Формулировки инвариантов от агента: сопоставляем по пути, на который он
    # сослался. Это уточнение смысла, а не новый факт, поэтому origin остаётся
    # INFERRED — как и у формулировки, выведенной из имени параметра.
    by_path = _invariants_by_path(invariants)
    # Родственные лимиты одного компонента — это одна политика, а не четыре.
    # Четыре отдельных guardrail «timeout», «timeout_graceful_shutdown»,
    # «timeout_keep_alive», «ws_ping_timeout» описывали одну настройку
    # HTTP-сервера и вытесняли из отчёта единственное содержательное
    # ограничение. Компонент в ключе обязателен: одноимённые таймауты из
    # разных мест защищают разные участки исполнения.
    by_key: dict[tuple[str, str], list[Candidate]] = {}
    for cand in candidates:
        if cand.type != "CONSTRAINT":
            continue
        fact = cand.facts[0]
        by_key.setdefault(_limit_family(fact), []).append(cand)

    for (component, family), cands in sorted(by_key.items()):
        facts = [fact for cand in cands for fact in cand.facts]
        values = sorted({f"{f.key}={_readable(f.value)}" for f in facts})
        paths = sorted({f.path for f in facts})
        keys = sorted({_norm_limit(f.key) for f in facts})
        obj = PkoObject(
            id=f"GRD-{len(out) + 1:03d}",
            kind="GUARDRAIL",
            name=_family_title(family, component, keys),
            candidates=[c.id for c in cands],
        )
        # Устойчивая идентичность политики: номер GRD-NNN зависит от позиции в
        # отсортированном списке и сдвигается при добавлении нового лимита, поэтому
        # сравнение версий опирается на ключ, а не на номер.
        obj.links["limit_key"] = [f"{component}|{family}"]
        proposed = _match_invariant(by_path, paths)
        obj.set("Защищаемый инвариант", proposed or _limit_title(family), "INFERRED", [])
        obj.set("Тип", _guard_type(family), "INFERRED", [])
        obj.set("Значение", " · ".join(values[:8]), "OBSERVED",
                [f.evidence(commit) for f in facts[:3]])
        _set_applies_to(obj, bbbs, paths, commit)
        _set_severity(obj, facts, commit)
        obj.set("Точка применения", paths[:5], "OBSERVED",
                [f.evidence(commit) for f in facts[:3]])
        obj.set("Ограничивающий эффект", "LIMIT_MODE / LOG — из кода не восстановлен", "UNKNOWN", [])
        _set_test_confirmation(obj, keys, extraction, commit)
        out.append(model.add(obj))

    # Отдельная политика «только чтение» — ключевой инвариант аналитических агентов.
    writes = extraction.select(taxonomy.is_mutating)
    reads = extraction.select(taxonomy.is_data_effect, action="read")
    if reads or writes:
        obj = PkoObject(id=f"GRD-{len(out) + 1:03d}", kind="GUARDRAIL",
                        name="Только чтение данных (SELECT)")
        obj.links["limit_key"] = [READ_ONLY_KEY]
        obj.set("Тип", _guard_type("read_only"), "INFERRED", [])
        _set_applies_to(obj, bbbs, sorted({f.path for f in (writes or reads)}), commit)
        _set_severity(obj, writes or reads, commit, read_only=True)
        if writes:
            runtime, migrations = policies.split_by_origin(writes)
            note = policies.origin_note(runtime, migrations)
            shown = (runtime or migrations)[:3]
            obj.set("Защищаемый инвариант", "Данные не изменяются автономным процессом",
                    "OBSERVED", [f.evidence(commit) for f in shown])
            obj.set("Фактическое состояние",
                    f"найден SQL, изменяющий данные: {len(writes)} мест"
                    + (f" ({note})" if note else ""),
                    "OBSERVED", [f.evidence(commit) for f in shown])
        else:
            obj.set("Защищаемый инвариант", "Данные не изменяются автономным процессом",
                    "OBSERVED", [f.evidence(commit) for f in reads[:3]])
            obj.set("Фактическое состояние",
                    "изменяющий SQL статически не найден", "OBSERVED",
                    [f.evidence(commit) for f in reads[:3]])
        obj.set("Точка применения", sorted({f.path for f in (writes or reads)})[:5], "OBSERVED",
                [f.evidence(commit) for f in (writes or reads)[:3]])
        _set_test_confirmation(obj, READ_ONLY_KEY, extraction, commit)
        out.append(model.add(obj))
    return out


# Смысл ограничения по семейству. Читателю карточки нужно понимать, от чего
# оно защищает, а не только как называется параметр.
_GUARD_TYPES = {
    "timeout": "Производительность и предсказуемость",
    "retries": "Производительность и предсказуемость",
    "rounds": "Производительность и предсказуемость",
    "rows": "Объём выдачи",
    "tokens": "Объём выдачи",
    "allowlist": "Безопасность и целостность данных",
    "read_only": "Безопасность и целостность данных",
}


def _readable(value: Any) -> str:
    """Значение ограничения по-человечески.

    Перечень разрешённого хранится списком, и `f"{key}={value}"` печатал
    питоновский `repr` с кавычками и скобками — в отчёте это выглядит как
    отладочный вывод, а не как значение политики.
    """
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _guard_type(family: str) -> str:
    return _GUARD_TYPES.get(family, "Ограничение исполнения")


def _set_applies_to(
    obj: PkoObject, bbbs: list[PkoObject] | None, paths: list[str], commit: str
) -> None:
    """Блоки, на которые ограничение распространяется.

    Выводится по тем же путям, что и владение атомарной операцией, поэтому
    ответ согласован с остальной моделью. Пусто — значит, ограничение живёт
    вне известных блоков (например, в общих настройках); пустое значение
    честнее натянутой привязки.
    """
    owners: list[str] = []
    for path in paths:
        owner = _owning_bbb(bbbs or [], path)
        if owner is not None and owner.id not in owners:
            owners.append(owner.id)
    if owners:
        obj.links["bbb"] = owners
        obj.set("Применяется к", ", ".join(owners), "INFERRED", [])
    else:
        obj.set("Применяется к", "блок не определён: ограничение задано вне известных блоков",
                "UNKNOWN", [])


def _set_severity(
    obj: PkoObject, facts: list[Fact], commit: str, read_only: bool = False
) -> None:
    """Насколько тяжело нарушение — по последствию проверки Gate.

    Значение не назначается на глаз: оно повторяет `fail_effect` той проверки,
    которая это ограничение потребляет (`pko.gate.evaluate.CHECKS`). Ограничение
    «только чтение» питает CHK-GRD-001 с последствием DENY, числовые лимиты —
    CHK-GRD-002 с последствием LIMIT_MODE. Всё остальное ни одна проверка не
    берёт, и вместо выдуманной степени тяжести пишем это прямо.
    """
    if read_only:
        obj.set("Severity", "blocker — нарушение ведёт к отказу в допуске (CHK-GRD-001)",
                "INFERRED", [])
        return
    if any(f.facets.mechanism == "limit" for f in facts):
        obj.set("Severity", "error — отсутствие ограничивает режим до ASSIST (CHK-GRD-002)",
                "INFERRED", [])
        return
    obj.set("Severity", "не участвует в решении о допуске", "INFERRED", [])


def _set_test_confirmation(
    obj: PkoObject, limit_keys: list[str] | str, extraction: Extraction, commit: str
) -> None:
    """Проставить подтверждение тестом по единой связи `pko.checks.test_link`.

    Тот же ответ получает проверка Gate, поэтому карточка допуска и паспорт
    ограничения не могут разойтись.

    Политика может объединять несколько параметров одного семейства. Тогда
    подтверждением считается покрытие всех: тест на один таймаут не доказывает
    остальные, а «подтверждено» на такой политике читалось бы как доказанность
    всего семейства.
    """
    keys = [limit_keys] if isinstance(limit_keys, str) else list(limit_keys)
    per_key = {key: confirming_cases(key, extraction) for key in keys}
    covered = [key for key, cases in per_key.items() if cases]

    if len(covered) != len(keys) or not keys:
        missing = [key for key in keys if key not in covered]
        detail = "нет"
        if covered:
            detail = f"частично: не подтверждены {', '.join(missing)}"
        obj.set("Подтверждён тестом", detail, "UNKNOWN", [])
        return

    reports = extraction.by_kind("TEST_REPORT")
    cases = sorted({case for found in per_key.values() for case in found})
    obj.set(
        "Подтверждён тестом",
        ", ".join(cases[:4]),
        "VERIFIED",
        [f.evidence(commit) for f in reports[:2]],
    )


# --- пробелы ---------------------------------------------------------------
def _gaps(model: PkoModel, extraction: Extraction, intent: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    if not intent:
        gaps.append(
            "Не найден business_intent.yaml: потребность, целевое состояние и владелец "
            "результата не подтверждены — отчёт является черновиком."
        )
    if not extraction.by_kind("TEST_REPORT"):
        gaps.append(
            "Нет готового отчёта о тестах (JUnit XML): ни одно ограничение не может "
            "получить статус «подтверждено тестом»."
        )
    mutating = extraction.select(taxonomy.is_mutating)
    if mutating:
        runtime, migrations = policies.split_by_origin(mutating)
        note = policies.origin_note(runtime, migrations)
        gaps.append(
            f"Найден SQL, изменяющий данные ({len(mutating)} мест"
            + (f", {note}" if note else "")
            + "): инвариант «только чтение» не выполняется."
        )
    # Изменения данных механизмами вне периметра проверки. Вердикт «только
    # чтение» их не видит, поэтому без этой строки отчёт выглядел бы чистым
    # при известном изменении данных.
    unguarded = [
        f for f in extraction.select(taxonomy.is_effect, action="write")
        if f.facets.mechanism in taxonomy.UNGUARDED_DATA_MECHANISMS
    ]
    if unguarded:
        mechanisms = ", ".join(sorted({f.facets.mechanism for f in unguarded}))
        gaps.append(
            f"Найдены изменения данных вне периметра проверки «только чтение» "
            f"({len(unguarded)} мест, механизмы: {mechanisms}): "
            f"CHK-GRD-001 их не учитывает."
        )
    if not extraction.select(taxonomy.is_step) and not extraction.by_kind("GRAPH"):
        gaps.append("Граф исполнения статически не обнаружен: траектория процесса не восстановлена.")
    ratio = extraction.coverage.ratio
    if ratio < 0.6:
        gaps.append(
            f"Проанализировано {ratio:.0%} файлов репозитория — часть системы осталась вне анализа."
        )
    for note in extraction.notes:
        gaps.append(note)
    return gaps


# --- вспомогательное -------------------------------------------------------
def _intent_evidence(intent: dict[str, Any], key: str) -> list[Evidence]:
    if not intent or key not in intent or intent.get(key) in (None, ""):
        return []
    src = intent.get("__source__", "business_intent.yaml")
    return [Evidence(commit=intent.get("__commit__", ""), path=src, line=None,
                     basis=f"подтверждено владельцем в поле {key}")]


def _group_candidates(
    candidates: list[Candidate], bbb_groups: dict[str, list[str]] | None
) -> dict[str, list[Candidate]]:
    """Собрать группы кандидатов: по предложению сборщика либо по пакетам."""
    capabilities = [c for c in candidates if c.type == "CAPABILITY"]
    by_id = {c.id: c for c in capabilities}

    if not bbb_groups:
        groups: dict[str, list[Candidate]] = {}
        for cand in capabilities:
            pkg = _package_of(cand.group)
            if not pkg or pkg.rsplit("/", 1)[-1] in SKIP_PACKAGES:
                continue
            groups.setdefault(pkg, []).append(cand)
        return groups

    groups = {}
    assigned: set[str] = set()
    for name, ids in bbb_groups.items():
        members = [by_id[i] for i in ids if i in by_id and i not in assigned]
        if not members:
            continue
        groups[name] = members
        assigned.update(c.id for c in members)

    # Кандидаты, которых сборщик не распределил, не теряются: они уходят в блок по пакету.
    for cand in capabilities:
        if cand.id in assigned:
            continue
        pkg = _package_of(cand.group)
        if not pkg or pkg.rsplit("/", 1)[-1] in SKIP_PACKAGES:
            continue
        groups.setdefault(_package_title(pkg), []).append(cand)
    return groups


def _facets_of(cand: Candidate) -> taxonomy.Facets:
    return cand.facts[0].facets if cand.facts else taxonomy.facets_for(cand.subtype)


def _category_of(cand: Candidate) -> str:
    return _facets_of(cand).category


def _mechanism_of(cand: Candidate) -> str:
    return _facets_of(cand).mechanism


def _is_meaningful(cand: Candidate) -> bool:
    """Несёт ли кандидат собственное поведение, а не только имя каталога."""
    facets = _facets_of(cand)
    return facets.category in (taxonomy.ENTRYPOINT, taxonomy.STEP) or (
        facets.category == taxonomy.COMPONENT and facets.mechanism != "package"
    )


# Как важен метод при выборе представителя ресурса: создание говорит о
# назначении системы больше, чем удаление.
_METHOD_RANK = {"POST": 0, "PUT": 1, "PATCH": 2, "GET": 3, "DELETE": 4}

_ROUTE_KEY = re.compile(r"^(?P<method>[A-Z]+)\s+(?P<path>\S+)$")


def _representative_entrypoints(
    routes: list[Fact], limit: int
) -> tuple[list[Fact], int]:
    """Выбрать по одному входу на ресурс, а не первые по алфавиту.

    Сортировка по строке ставила первыми все `DELETE ...`, и «Признаки
    распознавания» потребности выглядели как список удалений — при том, что
    evidence рядом показывал совсем другие строки. Здесь один представитель на
    ресурс и приоритет создающих методов, а полный перечень остаётся в блоках
    и в `semantic_facts.json`.
    """
    if not routes:
        return [], 0

    by_resource: dict[tuple[str, str], list[Fact]] = {}
    for fact in routes:
        by_resource.setdefault(_resource_of(fact), []).append(fact)

    chosen: list[Fact] = []
    for resource in sorted(by_resource):
        group = sorted(by_resource[resource], key=_entry_order)
        chosen.append(group[0])
    chosen.sort(key=_entry_order)
    return chosen[:limit], len(routes)


def _resource_of(fact: Fact) -> tuple[str, str]:
    """Механизм и ресурс входа: `POST /chats/{id}` и `GET /chats/` — один ресурс."""
    mechanism = fact.facets.mechanism
    match = _ROUTE_KEY.match(str(fact.key).strip())
    if not match:
        return mechanism, str(fact.key).strip().lower()
    path = re.sub(r"\{[^}]*\}", "{}", match.group("path")).rstrip("/").lower()
    return mechanism, path or "/"


def _entry_order(fact: Fact) -> tuple[int, str, str]:
    match = _ROUTE_KEY.match(str(fact.key).strip())
    method = match.group("method") if match else ""
    return _METHOD_RANK.get(method, 5), _resource_of(fact)[1], str(fact.key)


def _with_total(values: list[str], total: int) -> list[str]:
    """Дописать «и ещё N», если показана выборка, а не весь перечень."""
    if total > len(values):
        return values + [f"… всего входов: {total}"]
    return values


def _module_weight(modules: list[Candidate]) -> int:
    """Сколько файлов стоит за модулями-кандидатами.

    У факта от экстрактора значение — словарь `{"files": N}`, а у факта от агента
    это формулировка строкой. Форму значения задаёт источник факта, поэтому здесь
    её нельзя предполагать: неизвестная форма весит один файл.
    """
    total = 0
    for cand in modules:
        value = cand.facts[0].value if cand.facts else None
        if isinstance(value, dict):
            files = value.get("files", 1)
            total += files if isinstance(files, int) else 1
        else:
            total += 1
    return total


def _common_package(cands: list[Candidate]) -> str:
    """Общий пакет группы — нужен, чтобы связать атомарные операции с блоком."""
    packages = [_package_of(c.group) for c in cands if c.group]
    packages = [p for p in packages if p]
    if not packages:
        return ""
    return min(set(packages), key=lambda p: (len(p.split("/")), p))


def _package_of(group: str) -> str:
    parts = [p for p in group.split("/") if p]
    if not parts:
        return ""
    # Отбрасываем технические корни, чтобы группировать по смысловому пакету.
    trimmed = [p for p in parts if p not in {"src", "backend", "app", "lib"}]
    if not trimmed:
        return "/".join(parts)
    return "/".join(parts[: len(parts) - len(trimmed) + 1])


def _package_title(pkg: str) -> str:
    name = pkg.rsplit("/", 1)[-1]
    return PACKAGE_TITLES.get(name, f"Модуль «{name}»")


def _exec_kinds(routes: list[Candidate], tools: list[Candidate], nodes: list[Candidate]) -> list[str]:
    kinds: list[str] = []
    if routes:
        kinds.append("HTTP API")
    if tools:
        kinds.append("инструмент агента")
    if nodes:
        kinds.append("узел графа")
    return kinds


def _owning_bbb(bbbs: list[PkoObject], path: str) -> PkoObject | None:
    """Блок, который точнее всех накрывает путь факта.

    Порядок блоков в списке зависит от имён групп, придуманных сборщиком, и
    владение по нему решаться не должно: переименование группы иначе
    перебрасывало бы атомарную операцию другому блоку, а вместе с ней менялся
    бы её `stable_key` — и операция выглядела бы новой при сравнении версий.
    Поэтому сначала более длинное совпадение, затем — путь кандидата важнее
    пакета, и в последнюю очередь устойчивое имя ключа.
    """
    best: PkoObject | None = None
    best_score: tuple[int, int, str] | None = None
    for b in bbbs:
        for exact, source in ((1, "owned_paths"), (0, "package")):
            for prefix in b.links.get(source) or []:
                if not prefix or not (path == prefix or path.startswith(prefix + "/")):
                    continue
                key = (b.links.get("stable_key") or [b.id])[0]
                # Меньший ключ выигрывает — поэтому в оценке он с минусом
                # неудобен; сравниваем кортежем и разрешаем ничью явно.
                score = (len(prefix), exact, key)
                if best_score is None or _better(score, best_score):
                    best, best_score = b, score
    return best


def _better(score: tuple[int, int, str], current: tuple[int, int, str]) -> bool:
    """Длиннее совпадение → точнее источник → меньше `stable_key`."""
    if score[:2] != current[:2]:
        return score[:2] > current[:2]
    return score[2] < current[2]


def _all_packages(cands: list[Candidate]) -> list[str]:
    """Все пакеты группы, от короткого к длинному — порядок устойчив между прогонами."""
    packages = {_package_of(c.group) for c in cands if c.group}
    return sorted((p for p in packages if p), key=lambda p: (len(p.split("/")), p))


# Подпись эффекта входит в `stable_key` атомарной операции, поэтому у прежних
# механизмов формулировка обязана остаться дословно той же: любая правка текста
# показала бы все операции новыми при сравнении версий.
_EFFECT_LABELS = {
    ("sql", "read"): "Чтение данных SQL-запросом",
    ("sql", "write"): "Изменение данных SQL-запросом",
    ("llm", "call"): "Вызов языковой модели",
    ("orm", "read"): "Чтение данных через ORM",
    ("orm", "write"): "Изменение данных через ORM",
    ("fs", "read"): "Чтение файла",
    ("fs", "write"): "Запись файла",
    ("object_storage", "read"): "Чтение из объектного хранилища",
    ("object_storage", "write"): "Запись в объектное хранилище",
    ("queue", "emit"): "Отправка сообщения в очередь",
    ("queue", "read"): "Чтение сообщения из очереди",
    ("http_client", "call"): "Вызов внешнего HTTP-сервиса",
}


def _effect_label(fact: Fact) -> str:
    facets = fact.facets
    # `EXTERNAL` проверяется до таблицы: у него подпись называет саму систему,
    # и уточнённый механизм (`openai` → llm) не должен её подменять.
    if fact.kind == "EXTERNAL" or facets.mechanism == "integration":
        return f"Обращение к внешней системе: {fact.key}"
    known = _EFFECT_LABELS.get((facets.mechanism, facets.action))
    if known:
        return known
    # Механизм, для которого подписи нет: называем его прямо, чтобы читатель
    # видел, что именно наблюдалось, а не обобщение.
    action = {"read": "чтение", "write": "изменение", "call": "вызов",
              "emit": "отправка"}.get(facets.action, "обращение")
    return f"Внешний эффект: {action} ({facets.mechanism or 'механизм не определён'})"


def _invariants_by_path(invariants: list[dict[str, Any]] | None) -> dict[str, str]:
    """Индекс «путь → формулировка инварианта» из предложений агента."""
    out: dict[str, str] = {}
    for item in invariants or []:
        path = str(item.get("path") or "").strip()
        text = str(item.get("invariant") or "").strip()
        if path and text:
            out.setdefault(path, text)
    return out


def _match_invariant(by_path: dict[str, str], paths: list[str]) -> str:
    """Взять формулировку, если агент сослался на тот же файл, где задано ограничение."""
    for path in paths:
        if path in by_path:
            return by_path[path]
    return ""


def _norm_limit(key: str) -> str:
    return re.sub(r"^(default_|app_|max_)", "", key.strip().lower())


# Семейства родственных ограничений: разные ключи одной настройки.
_LIMIT_FAMILIES = (
    ("timeout", ("timeout", "ping_timeout", "keep_alive")),
    ("rows", ("rows", "row_limit", "page_size", "batch_size")),
    ("retries", ("retries", "retry", "attempts")),
    ("rounds", ("rounds", "iterations")),
    ("tokens", ("tokens",)),
    ("allowlist", ("allowed", "allowlist", "whitelist", "permitted")),
)


def _limit_family(fact: Fact) -> tuple[str, str]:
    """Компонент и семейство ограничения — ключ объединения в одну политику."""
    key = _norm_limit(str(fact.key))
    family = next((name for name, markers in _LIMIT_FAMILIES
                   if any(m in key for m in markers)), key)
    return _package_of(fact.path.rsplit("/", 1)[0]), family


def _family_title(family: str, component: str, keys: list[str]) -> str:
    """Имя политики: смысл плюс компонент, а не имя одного параметра."""
    title = _limit_title(family)
    where = _package_title(component) if component else ""
    if len(keys) == 1:
        return f"{title} ({keys[0]})"
    return f"{title} — {where}" if where else f"{title} ({len(keys)} параметра)"


def _limit_title(key: str) -> str:
    titles = {
        "timeout": "Ограничение времени исполнения",
        "rows": "Ограничение объёма выдачи",
        "max_rows": "Ограничение объёма выдачи",
        "row_limit": "Ограничение объёма выдачи",
        "retries": "Ограничение числа повторов",
        "max_retries": "Ограничение числа повторов",
        "rounds": "Ограничение числа циклов",
        "limit": "Числовое ограничение",
        "tokens": "Ограничение объёма ответа модели",
        "allowlist": "Перечень разрешённого",
        "allowed_tables": "Перечень разрешённых таблиц",
    }
    for marker, title in titles.items():
        if marker in key:
            return title
    return f"Ограничение «{key}»"


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").upper()
    return cleaned or "SYSTEM"
