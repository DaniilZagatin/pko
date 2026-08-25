"""Текст отчёта.

Роль писателя (DeepSeek-V4-Flash) — только формулировки. На вход ему уходит
готовая PKO-модель в компактном JSON: ни кода, ни промптов, ни содержимого
конфигураций. Любая правка фактов невозможна по построению, а попытку ввести
несуществующий идентификатор ловит `pko.report.guard`.

Без настроенного writer отчёт собирается детерминированным шаблоном. Если
writer явно включён, его пустой или не прошедший сторожа ответ прерывает
прогон: молчаливая подмена авторства отчёта недопустима.
"""

from __future__ import annotations

import json
import re
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
    "не установлено, так и пиши. Без списков, без markdown-заголовков. "
    "Не более четырёх предложений и не более 600 знаков: это шапка отчёта, "
    "а не пересказ модели — подробности читатель возьмёт из паспортов."
)

# Потолок обзора в шапке. Инструкция в промпте — основной механизм, обрезка —
# страховка: на реальном прогоне модель вернула один абзац на ~2300 знаков,
# и он занял в отчёте больше места, чем всё остальное вместе.
OVERVIEW_LIMIT = 600
OVERVIEW_SENTENCES = 4


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
    written = _guarded(spec, user, model)
    return WrittenText(shorten(written.text), written.source, written.notes)


def shorten(text: str, limit: int = OVERVIEW_LIMIT,
            sentences: int = OVERVIEW_SENTENCES) -> str:
    """Обрезать обзор по границе предложения.

    Обрыв на полуслове читается как сбой, поэтому режем только по точке.
    Если первое же предложение длиннее потолка, оставляем его целиком: лучше
    длинная фраза, чем оборванная.
    """
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    kept: list[str] = []
    for part in parts[:sentences]:
        candidate = " ".join(kept + [part])
        if kept and len(candidate) > limit:
            break
        kept.append(part)
    return " ".join(kept)


# Пояснение к объекту: одно-два предложения деловым языком. Отдельная система
# нужна потому, что у обзора другая задача — там про систему целиком, а здесь
# читатель стоит перед конкретным блоком и спрашивает «что это и зачем».
_OBJECT_SYSTEM = (
    "Ты пишешь пояснения к карточкам объектов управления на русском языке. "
    "На каждый объект — одно-два предложения деловым языком: что это в работе "
    "системы и почему это важно владельцу процесса. Опирайся только на "
    "переданные поля; не добавляй идентификаторы, числа, названия файлов и "
    "выводы, которых там нет. Не пересказывай поля дословно и не используй "
    "технический жаргон без нужды. Если поля не дают понять смысл, так и "
    "напиши одной фразой. Ответ — JSON-объект вида {\"BBB-001\": \"текст\"} "
    "без пояснений вокруг."
)


def write_object_notes(model: PkoModel, spec: ModelSpec | None) -> tuple[dict[str, str], list[str]]:
    """Короткое пояснение к каждому объекту: id → текст.

    Паспорт состоит из полей и координат; человеку, который открыл его впервые,
    неоткуда узнать, что означает блок «Оркестрация графа исполнения» и зачем
    ему атомарная операция «Чтение данных SQL-запросом». Крючок для такого
    текста в рендере был, но никто его не наполнял.
    """
    if spec is None or not model.objects:
        return {}, []

    payload = [
        {
            "id": o.id,
            "kind": o.kind_title,
            "name": o.name,
            "fields": {k: v.text()[:160] for k, v in list(o.fields.items())[:6]},
        }
        for o in model.objects
    ]
    user = (
        "Поясни каждый объект по его полям.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    raw = ChatClient(spec=spec).complete(system=_OBJECT_SYSTEM, user=user)

    notes = _parse_notes(raw, {o.id for o in model.objects})
    if not notes:
        return {}, ["Пояснения к объектам не разобраны: модель ответила не JSON-объектом"]

    allowed = model.ids()
    clean: dict[str, str] = {}
    rejected: list[str] = []
    for obj_id, text in notes.items():
        violations = check_text(text, allowed_ids=allowed, allowed_paths=set())
        if violations:
            rejected.append(obj_id)
            continue
        clean[obj_id] = text
    if rejected:
        return clean, [
            "Пояснения отброшены сторожем для: " + ", ".join(sorted(rejected)[:5])
        ]
    return clean, []


def _parse_notes(raw: str, known_ids: set[str]) -> dict[str, str]:
    """Достать словарь пояснений из ответа модели."""
    match = re.search(r"\{[\s\S]*\}", raw or "")
    if not match:
        return {}
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(k): str(v).strip()
        for k, v in data.items()
        if str(k) in known_ids and str(v).strip()
    }


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
    # Diff содержит и удалённые объекты старой версии. Они присутствуют во
    # входе writer и потому не являются выдумкой, даже если в current-модели
    # их уже нет.
    diff_ids = model.ids() | {o.id for o in diff.objects}
    return _guarded(spec, user, model, allowed_ids=diff_ids)


def _guarded(
    spec: ModelSpec,
    user: str,
    model: PkoModel,
    allowed_ids: set[str] | None = None,
) -> WrittenText:
    # Отказ модели больше не превращается в шаблон: запуск с `--llm` — это
    # заявка на текст модели, и молчаливая подмена шаблоном скрывала от
    # читателя, что отчёт написан не тем, чем он думает. Ошибка поднимается
    # выше и прерывает прогон до публикации комплекта.
    text = ChatClient(spec=spec).complete(system=_SYSTEM, user=user).strip()

    allowed_paths = _allowed_paths(model, user)
    violations = check_text(
        text,
        allowed_ids=allowed_ids if allowed_ids is not None else model.ids(),
        allowed_paths=allowed_paths,
    )
    if violations:
        # При явном LLM-режиме тихий откат на шаблон так же недопустим, как
        # пустой ответ: пользователь должен исправить endpoint/промпт и
        # повторить прогон, а не получить артефакт с неочевидным авторством.
        raise LlmError(
            "Текст модели не прошёл проверку отчёта.",
            "Сторож отклонил ответ: " + "; ".join(v.render() for v in violations),
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
