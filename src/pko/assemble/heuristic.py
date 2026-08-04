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
    process = _build_process(model, slug, journey, bbbs, extraction, intent, commit)
    _build_operations(model, bbbs, extraction, commit)
    guardrails = _build_guardrails(model, candidates, extraction, commit)

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

    routes = extraction.by_kind("ROUTE")
    obj.set(
        "Признаки распознавания",
        [f.key for f in sorted(routes, key=lambda x: x.key)[:8]] or None,
        "OBSERVED" if routes else "UNKNOWN",
        [f.evidence(commit) for f in routes[:3]],
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
) -> PkoObject:
    nodes = extraction.by_kind("GRAPH_NODE")
    edges = extraction.by_kind("GRAPH_EDGE")
    routes = extraction.by_kind("ROUTE")
    graph = extraction.by_kind("GRAPH")

    obj = PkoObject(id=f"AP-CP-{slug}-001", kind="PROCESS",
                    name="Автономный процесс клиентского пути")
    obj.links["journey"] = [journey.id]

    obj.set("Связанный клиентский путь", journey.id, "INFERRED", [])
    obj.set(
        "Условия запуска",
        [f.key for f in sorted(routes, key=lambda x: x.key)[:10]] or None,
        "OBSERVED" if routes else "UNKNOWN",
        [f.evidence(commit) for f in routes[:3]],
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
        for kind in ("EXTERNAL", "SQL_READ", "SQL_WRITE", "LLM_CALL")
        for f in extraction.by_kind(kind)
    }

    out: list[PkoObject] = []
    for i, (group_name, cands) in enumerate(sorted(groups.items()), start=1):
        meaningful = [c for c in cands if c.subtype in {"ROUTE", "GRAPH_NODE", "TOOL"}]
        modules = [c for c in cands if c.subtype == "MODULE"]
        has_effect = any(_package_of(c.group) in effect_packages for c in cands)
        if (
            not meaningful
            and not has_effect
            and sum(c.facts[0].value.get("files", 0) for c in modules) < 2
        ):
            continue

        pkg = _common_package(cands)
        obj = PkoObject(
            id=f"BBB-{len(out) + 1:03d}",
            kind="BBB",
            name=group_name if bbb_groups else _package_title(pkg),
            candidates=[c.id for c in sorted(cands, key=lambda x: x.id)],
        )
        obj.links["package"] = [pkg]
        # Номер BBB-NNN позиционный: новый пакет сдвигает нумерацию всех следующих
        # блоков. Сравнение версий опирается на пакет, а не на номер.
        obj.links["stable_key"] = [pkg or obj.name]

        routes = [c for c in cands if c.subtype == "ROUTE"]
        tools = [c for c in cands if c.subtype == "TOOL"]
        nodes = [c for c in cands if c.subtype == "GRAPH_NODE"]

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
    """Атомарная операция — минимальный шаг с проверяемым внешним эффектом (§4.5)."""
    effect_kinds = ("EXTERNAL", "SQL_READ", "SQL_WRITE", "LLM_CALL")
    grouped: dict[tuple[str, str, str], list[Fact]] = {}
    for kind in effect_kinds:
        for fact in extraction.by_kind(kind):
            owner = _owning_bbb(bbbs, fact.path)
            bbb_id = owner.id if owner else ""
            package = (owner.links.get("package") or [""])[0] if owner else ""
            label = _effect_label(kind, fact)
            grouped.setdefault((package, label, bbb_id), []).append(fact)

    out: list[PkoObject] = []
    for i, ((package, label, bbb_id), facts) in enumerate(sorted(grouped.items()), start=1):
        obj = PkoObject(id=f"AO-{i:03d}", kind="AO", name=label)
        # Как и у BBB, номер позиционный: устойчивая идентичность операции — это
        # пакет владельца плюс проверяемый эффект.
        obj.links["stable_key"] = [f"{package}|{label}"]
        if bbb_id:
            obj.links["bbb"] = [bbb_id]
        # Принадлежность блоку выведена по пакету, а не найдена отдельным фактом.
        obj.set("Связанный BBB", bbb_id or "не определён",
                "INFERRED" if bbb_id else "UNKNOWN", [])
        obj.set("Проверяемый эффект", label, "OBSERVED", [f.evidence(commit) for f in facts[:3]])
        obj.set("Количество мест вызова", len(facts), "OBSERVED",
                [f.evidence(commit) for f in facts[:3]])
        obj.set("Повтор и компенсация", "правило не найдено в коде", "UNKNOWN", [])
        obj.set("Владелец и аудит", None, "UNKNOWN", [])
        out.append(model.add(obj))
    return out


# --- guardrails ------------------------------------------------------------
def _build_guardrails(
    model: PkoModel, candidates: list[Candidate], extraction: Extraction, commit: str
) -> list[PkoObject]:
    out: list[PkoObject] = []
    by_key: dict[str, list[Candidate]] = {}
    for cand in candidates:
        if cand.type != "CONSTRAINT":
            continue
        by_key.setdefault(_norm_limit(cand.facts[0].key), []).append(cand)

    for key, cands in sorted(by_key.items()):
        facts = [fact for cand in cands for fact in cand.facts]
        values = sorted({str(f.value) for f in facts})
        paths = sorted({f.path for f in facts})
        obj = PkoObject(
            id=f"GRD-{len(out) + 1:03d}",
            kind="GUARDRAIL",
            name=f"{_limit_title(key)} ({key})",
            candidates=[c.id for c in cands],
        )
        # Устойчивая идентичность политики: номер GRD-NNN зависит от позиции в
        # отсортированном списке и сдвигается при добавлении нового лимита, поэтому
        # сравнение версий опирается на ключ, а не на номер.
        obj.links["limit_key"] = [key]
        obj.set("Защищаемый инвариант", _limit_title(key), "INFERRED", [])
        obj.set("Значение", " · ".join(values[:6]), "OBSERVED",
                [f.evidence(commit) for f in facts[:3]])
        obj.set("Точка применения", paths[:5], "OBSERVED",
                [f.evidence(commit) for f in facts[:3]])
        obj.set("Ограничивающий эффект", "LIMIT_MODE / LOG — из кода не восстановлен", "UNKNOWN", [])
        _set_test_confirmation(obj, key, extraction, commit)
        out.append(model.add(obj))

    # Отдельная политика «только чтение» — ключевой инвариант аналитических агентов.
    writes = extraction.by_kind("SQL_WRITE")
    reads = extraction.by_kind("SQL_READ")
    if reads or writes:
        obj = PkoObject(id=f"GRD-{len(out) + 1:03d}", kind="GUARDRAIL",
                        name="Только чтение данных (SELECT)")
        obj.links["limit_key"] = [READ_ONLY_KEY]
        if writes:
            obj.set("Защищаемый инвариант", "Данные не изменяются автономным процессом",
                    "OBSERVED", [f.evidence(commit) for f in writes[:3]])
            obj.set("Фактическое состояние",
                    f"найден SQL, изменяющий данные: {len(writes)} мест", "OBSERVED",
                    [f.evidence(commit) for f in writes[:3]])
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


def _set_test_confirmation(
    obj: PkoObject, limit_key: str, extraction: Extraction, commit: str
) -> None:
    """Проставить подтверждение тестом по единой связи `pko.checks.test_link`.

    Тот же ответ получает проверка Gate, поэтому карточка допуска и паспорт
    ограничения не могут разойтись.
    """
    cases = confirming_cases(limit_key, extraction)
    if not cases:
        obj.set("Подтверждён тестом", "нет", "UNKNOWN", [])
        return
    reports = extraction.by_kind("TEST_REPORT")
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
    if extraction.by_kind("SQL_WRITE"):
        gaps.append(
            f"Найден SQL, изменяющий данные ({len(extraction.by_kind('SQL_WRITE'))} мест): "
            "инвариант «только чтение» не выполняется."
        )
    if not extraction.by_kind("GRAPH_NODE") and not extraction.by_kind("GRAPH"):
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
    """Блок, чей пакет наиболее точно накрывает путь факта."""
    best: PkoObject | None = None
    best_len = -1
    for b in bbbs:
        pkg = (b.links.get("package") or [""])[0]
        if pkg and path.startswith(pkg + "/") and len(pkg) > best_len:
            best, best_len = b, len(pkg)
    return best


def _effect_label(kind: str, fact: Fact) -> str:
    if kind == "EXTERNAL":
        return f"Обращение к внешней системе: {fact.key}"
    if kind == "SQL_READ":
        return "Чтение данных SQL-запросом"
    if kind == "SQL_WRITE":
        return "Изменение данных SQL-запросом"
    return "Вызов языковой модели"


def _norm_limit(key: str) -> str:
    return re.sub(r"^(default_|app_|max_)", "", key.strip().lower())


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
        "allowed_tables": "Перечень разрешённых таблиц",
    }
    for marker, title in titles.items():
        if marker in key:
            return title
    return f"Ограничение «{key}»"


def _slug(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").upper()
    return cleaned or "SYSTEM"
