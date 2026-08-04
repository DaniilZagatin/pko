"""Текст отчёта.

Роль писателя (DeepSeek-V4-Flash) — только формулировки. На вход ему уходит
готовая PKO-модель в компактном JSON: ни кода, ни промптов, ни содержимого
конфигураций. Любая правка фактов невозможна по построению, а попытку ввести
несуществующий идентификатор ловит `pko.report.guard`.

Если endpoint не задан, недоступен или текст не прошёл сторожа — отчёт
собирается детерминированным шаблоном. Отчёт выпускается всегда; отсутствие
модели ухудшает читаемость, но не блокирует результат.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pko.diff.engine import ADDED, CHANGED, REMOVED, ModelDiff
from pko.errors import LlmError
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.model.schema import PkoModel
from pko.report.guard import PATH_PATTERN, check_text

_SYSTEM = (
    "Ты пишешь текст управленческого отчёта на русском языке по готовой модели системы. "
    "Правила жёсткие: используй только факты из переданного JSON; не добавляй новые "
    "идентификаторы, числа, названия файлов и выводы; не смягчай пробелы — если что-то "
    "не установлено, так и пиши. Без списков, без markdown-заголовков, 4–6 предложений."
)


@dataclass
class WrittenText:
    text: str
    source: str                      # "llm" | "template"
    notes: list[str] = field(default_factory=list)


def write_overview(model: PkoModel, spec: ModelSpec | None, thinking: bool = False) -> WrittenText:
    """Краткий обзор системы для таксономии и паспортов."""
    fallback = deterministic_overview(model)
    if spec is None:
        return WrittenText(fallback, "template")

    payload = _model_digest(model)
    user = (
        "Опиши, что это за система и что о ней достоверно известно. "
        "Обязательно упомяни долю неустановленных полей и главные пробелы.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return _guarded(spec, user, model, fallback)


def write_diff_narrative(
    diff: ModelDiff, model: PkoModel, spec: ModelSpec | None
) -> WrittenText:
    """Общая оценка изменений между версиями."""
    fallback = deterministic_diff_narrative(diff)
    if spec is None:
        return WrittenText(fallback, "template")

    payload = {
        "from": {"label": diff.left_label, "counts": diff.counts_left},
        "to": {"label": diff.right_label, "counts": diff.counts_right},
        "summary": diff.summary(),
        "changed_objects": [
            {"id": o.id, "kind": o.kind, "name": o.name, "status": o.status,
             "changed_fields": [c.label for c in o.changes][:6]}
            for o in diff.objects if o.status != "SAME"
        ][:40],
    }
    user = (
        "Опиши, как изменилась система между версиями. Не выдумывай причин изменений.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    return _guarded(spec, user, model, fallback)


def _guarded(spec: ModelSpec, user: str, model: PkoModel, fallback: str) -> WrittenText:
    try:
        text = ChatClient(spec=spec).complete(system=_SYSTEM, user=user).strip()
    except LlmError as exc:
        return WrittenText(fallback, "template", [f"Текст написан шаблоном: {exc.message}"])

    if not text:
        return WrittenText(fallback, "template", ["Модель вернула пустой ответ"])

    allowed_paths = _allowed_paths(model, user)
    violations = check_text(text, allowed_ids=model.ids(), allowed_paths=allowed_paths)
    if violations:
        return WrittenText(
            fallback,
            "template",
            ["Текст модели отброшен сторожем: " + "; ".join(v.render() for v in violations)],
        )
    return WrittenText(text, "llm")


def _allowed_paths(model: PkoModel, writer_input: str) -> set[str]:
    """Пути, которые writer действительно видел во входной модели.

    Evidence — не единственный источник: digest также содержит gaps и значения
    полей. Отсутствующий ``business_intent.yaml`` закономерно есть в gap, хотя
    evidence на несуществующий файл быть не может. Для внешнего intent evidence
    может быть абсолютным, а writer естественно повторит только basename.
    """
    paths = {ev.path for obj in model.objects for ev in obj.all_evidence() if ev.path}
    paths.update(PATH_PATTERN.findall(writer_input))
    expanded: set[str] = set()
    for path in paths:
        expanded.add(path)
        expanded.add(path.lstrip("/"))
        expanded.add(path.rsplit("/", 1)[-1])
    return {p for p in expanded if p}


# --- детерминированные формулировки ---------------------------------------
def deterministic_overview(model: PkoModel) -> str:
    counts = model.counts()
    meta = model.meta
    parts = [
        f"Репозиторий {meta.get('repo', '')} на коммите {str(meta.get('commit', ''))[:8]}: "
        f"восстановлено {counts['BBB']} переиспользуемых блоков, {counts['AO']} атомарных операций "
        f"и {counts['GUARDRAIL']} ограничений исполнения."
    ]
    parts.append(
        f"Проанализировано {model.coverage.ratio:.0%} файлов "
        f"({model.coverage.files_analyzed} из {model.coverage.files_total}); "
        f"фактов собрано {model.facts_count}."
    )
    parts.append(f"Доля полей без установленного значения — {model.unknown_ratio():.0%}.")
    if model.gaps:
        parts.append("Главный пробел: " + model.gaps[0])
    return " ".join(parts)


def deterministic_diff_narrative(diff: ModelDiff) -> str:
    s = diff.summary()
    changed_kinds = sorted({o.kind for o in diff.objects if o.status != "SAME"})
    parts = [
        f"Между версиями {diff.left_label} и {diff.right_label} добавлено {s[ADDED]} объектов, "
        f"удалено {s[REMOVED]}, изменено {s[CHANGED]}."
    ]
    if changed_kinds:
        parts.append("Затронуты типы объектов: " + ", ".join(changed_kinds) + ".")
    else:
        parts.append("Структура объектов управления не изменилась.")
    return " ".join(parts)


def _model_digest(model: PkoModel) -> dict[str, Any]:
    """Компактный вид модели для писателя: без доказательств и без кода."""
    return {
        "repo": model.meta.get("repo"),
        "commit": str(model.meta.get("commit", ""))[:8],
        "version": model.meta.get("version_label"),
        "counts": model.counts(),
        "coverage": {
            "ratio": round(model.coverage.ratio, 3),
            "analyzed": model.coverage.files_analyzed,
            "total": model.coverage.files_total,
        },
        "unknown_ratio": round(model.unknown_ratio(), 3),
        "gaps": model.gaps,
        "objects": [
            {
                "id": o.id,
                "kind": o.kind,
                "name": o.name,
                "fields": {k: v.text()[:200] for k, v in list(o.fields.items())[:6]},
            }
            for o in model.objects
        ],
    }
