"""Детерминированная проверка модели — вместо перекрёстной проверки языковой моделью.

Валидатор ловит ровно то, что можно поймать кодом: битые ссылки на файлы,
разорванные связи объектов (§8.10) и поля, которые заявляют наблюдение в коде,
но не приводят ни одного доказательства. Он не судит о смысле сборки — за это
отвечает человек, поэтому в паспорт BBB выписывается список кандидатов, из
которых блок собран.
"""

from __future__ import annotations

from dataclasses import dataclass

from pko.model.schema import PkoModel

ERROR = "ERROR"
WARN = "WARN"

# Связи, которые обязаны указывать на существующий объект модели. Всё прочее в
# `links` — техническая привязка: `package` (каталог) и `limit_key` (устойчивый
# идентификатор политики для сравнения версий).
REFERENCE_LINKS = frozenset({"need", "journey", "process", "bbb", "guardrail", "operation"})


@dataclass(frozen=True)
class Issue:
    level: str
    code: str
    message: str
    object_id: str = ""

    def render(self) -> str:
        where = f" [{self.object_id}]" if self.object_id else ""
        return f"{self.level}{where} {self.code}: {self.message}"


def validate(
    model: PkoModel,
    known_files: set[str] | None = None,
    external_paths: set[str] | None = None,
) -> list[Issue]:
    """Проверить модель. ERROR блокирует допуск, WARN попадает в пробелы отчёта.

    `external_paths` — источники, которые заведомо лежат вне анализируемого коммита
    (файл, переданный флагом `--intent`, отчёт JUnit). Их отсутствие в дереве
    репозитория ошибкой не является.
    """
    issues: list[Issue] = []
    issues.extend(_evidence_paths(model, known_files or set(), external_paths or set()))
    issues.extend(_origin_requires_evidence(model))
    issues.extend(_connectivity(model))
    issues.extend(_bbb_candidates(model))
    issues.extend(_guardrail_enforcement(model))
    return issues


def _evidence_paths(
    model: PkoModel, known_files: set[str], external_paths: set[str]
) -> list[Issue]:
    if not known_files:
        return []
    issues: list[Issue] = []
    for obj in model.objects:
        for label, fld in obj.fields.items():
            for ev in fld.evidence:
                # Пути вне репозитория: явно объявленные внешние источники плюс
                # абсолютные пути и отчёты о тестах.
                if ev.path in external_paths or ev.path.startswith("/") or ev.path.endswith(".xml"):
                    continue
                if ev.path in known_files:
                    continue
                if any(f.startswith(ev.path.rstrip("/") + "/") for f in known_files):
                    continue  # ссылка на пакет, а не на файл
                issues.append(
                    Issue(
                        ERROR,
                        "EVIDENCE_PATH_MISSING",
                        f"поле «{label}» ссылается на несуществующий путь {ev.path}",
                        obj.id,
                    )
                )
    return issues


def _origin_requires_evidence(model: PkoModel) -> list[Issue]:
    issues: list[Issue] = []
    for obj in model.objects:
        for label, fld in obj.fields.items():
            if fld.origin in {"OBSERVED", "VERIFIED"} and not fld.evidence and not fld.is_empty:
                issues.append(
                    Issue(
                        ERROR,
                        "ORIGIN_WITHOUT_EVIDENCE",
                        f"поле «{label}» заявлено как {fld.origin}, но не приводит ни одной ссылки",
                        obj.id,
                    )
                )
    return issues


def _connectivity(model: PkoModel) -> list[Issue]:
    """Инварианты связности §8.10."""
    issues: list[Issue] = []
    ids = model.ids()

    for obj in model.objects:
        for rel, targets in obj.links.items():
            # Проверяются только ссылки на объекты. Остальные записи в `links` —
            # техническая привязка (пакет, устойчивый ключ политики), и требовать
            # от них существующего объекта нельзя.
            if rel not in REFERENCE_LINKS:
                continue
            for target in targets:
                if target not in ids:
                    issues.append(
                        Issue(ERROR, "BROKEN_LINK",
                              f"связь «{rel}» указывает на неизвестный объект {target}", obj.id)
                    )

    for ao in model.by_kind("AO"):
        if not ao.links.get("bbb"):
            issues.append(
                Issue(WARN, "AO_WITHOUT_BBB",
                      "атомарная операция не отнесена ни к одному BBB", ao.id)
            )

    used_bbb = {b for p in model.by_kind("PROCESS") for b in p.links.get("bbb", [])}
    for bbb in model.by_kind("BBB"):
        if bbb.id not in used_bbb:
            issues.append(
                Issue(WARN, "BBB_NOT_USED", "BBB не вызывается ни одним автономным процессом", bbb.id)
            )

    for journey in model.by_kind("JOURNEY"):
        if not journey.links.get("need"):
            issues.append(Issue(ERROR, "JOURNEY_WITHOUT_NEED",
                                "клиентский путь не связан с потребностью", journey.id))
    for process in model.by_kind("PROCESS"):
        if not process.links.get("journey"):
            issues.append(Issue(ERROR, "PROCESS_WITHOUT_JOURNEY",
                                "автономный процесс не связан с клиентским путём", process.id))
    return issues


def _bbb_candidates(model: PkoModel) -> list[Issue]:
    issues: list[Issue] = []
    for bbb in model.by_kind("BBB"):
        if not bbb.candidates:
            issues.append(
                Issue(ERROR, "BBB_WITHOUT_CANDIDATES",
                      "блок не собран ни из одного найденного в коде кандидата", bbb.id)
            )
    return issues


def _guardrail_enforcement(model: PkoModel) -> list[Issue]:
    """§4.6.5: у политики должна быть точка применения, иначе это не ограничение, а пожелание."""
    issues: list[Issue] = []
    for grd in model.by_kind("GUARDRAIL"):
        point = grd.fields.get("Точка применения")
        if point is None or point.is_empty:
            issues.append(
                Issue(WARN, "GUARDRAIL_WITHOUT_POINT",
                      "не найдена точка, в которой ограничение применяется", grd.id)
            )
        # Подтверждение тестом ставит `pko.checks.test_link` при сборке модели.
        # Валидатор не вычисляет связь заново, иначе паспорт и Gate снова разойдутся.
        confirmed = grd.fields.get("Подтверждён тестом")
        if confirmed is None or confirmed.origin != "VERIFIED":
            issues.append(
                Issue(WARN, "GUARDRAIL_NOT_TESTED",
                      "ограничение не подтверждено негативным тестом — статус не выше OBSERVED",
                      grd.id)
            )
    return issues
