"""Бизнес-интерпретация детерминированного сравнения (роль `reporter`).

Принцип плана версионирования (§10): факт изменения — код
(`versioning/diff.py`), смысл изменения — LLM. Один запрос на всё сравнение
сразу (не по одному на этап) — тот же принцип, что и у
`progress/summarize.py::summarize_progress` (единственный вызов reporter по
итогам всех вердиктов).

Роль необязательна, как и у `summarize_progress`: без настроенного reporter
или при сбое LLM сравнение остаётся полностью рабочим — просто без прозы
(`progress_summary`/`stage_business_deltas`/`current_risks`/`next_focus`
пустые), причина всегда в `notes`, ничего не подставляется шаблоном.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pko.errors import LlmError
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.schema import ItemVerdict, ProgressModel
from pko.report.guard import check_text
from pko.versioning.diff import VersionComparison

_SYSTEM = (
    "Ты объясняешь руководителю, что изменилось в продукте между двумя проверками "
    "готовности. Тебе даны факты изменения по каждому этапу (посчитаны кодом, не тобой: "
    "предыдущий и текущий статус/готовность, тип изменения) и пояснения агента по каждому "
    "этапу из обеих проверок. Не переопределяй сами факты — статус и тип изменения уже "
    "решены кодом, твоя задача объяснить их смысл простым языком для руководителя.\n"
    "Верни строго один JSON-объект (без пояснений вокруг, без markdown-обёртки) такой формы:\n"
    '{"progress_summary": "3-5 предложений о периоде в целом", '
    '"stage_business_deltas": {"<canonical_stage_id>": "1-2 предложения: что изменилось по '
    'этапу и что остаётся"}, '
    '"risks": [{"text": "риск на русском простым языком", '
    '"state": "NEW"|"PERSISTING"|"RESOLVED"}], '
    '"next_focus": ["короткий пункт", "..."]}\n'
    "Используй только сведения, данные ниже — не выдумывай названия этапов, пути к файлам "
    "или формулировки сверх того, что тебе передано. Не упоминай пути к файлам, имена "
    "функций/классов/эндпоинтов и тесты — это язык кода, не бизнес-объяснения."
)


@dataclass
class InterpretedComparison:
    progress_summary: str = ""
    stage_business_deltas: dict[str, str] = field(default_factory=dict)
    current_risks: list[dict[str, str]] = field(default_factory=list)
    next_focus: list[str] = field(default_factory=list)
    source: str = "none"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "progress_summary": self.progress_summary,
            "stage_business_deltas": self.stage_business_deltas,
            "current_risks": self.current_risks,
            "next_focus": self.next_focus,
        }


def interpret_comparison(
    comparison: VersionComparison,
    from_model: ProgressModel,
    to_model: ProgressModel,
    spec: ModelSpec | None,
    client: ChatClient | None = None,
) -> InterpretedComparison:
    """Сформировать бизнес-интерпретацию сравнения. Без роли/при сбое —

    пустой результат с причиной в `notes`, не шаблон (тот же принцип, что и
    `progress/summarize.py::summarize_progress`).
    """
    if spec is None:
        return InterpretedComparison(
            notes=["Бизнес-интерпретация не сформирована: роль reporter не настроена"]
        )
    if not comparison.stage_deltas:
        return InterpretedComparison(
            notes=["Бизнес-интерпретация не сформирована: нет изменений между снимками"]
        )

    user = json.dumps(_digest(comparison, from_model, to_model), ensure_ascii=False, indent=2)
    chat_client = client if client is not None else ChatClient(spec=spec)
    try:
        text = chat_client.complete(system=_SYSTEM, user=user, max_tokens=1500)
    except LlmError as exc:
        return InterpretedComparison(
            notes=[f"Бизнес-интерпретация не сформирована: reporter недоступен: {exc.message}"]
        )

    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return InterpretedComparison(notes=["Бизнес-интерпретация отклонена: ответ не является JSON"])
    if not isinstance(raw, dict):
        return InterpretedComparison(notes=["Бизнес-интерпретация отклонена: ответ не является объектом"])

    allowed_stage_ids = {d.canonical_stage_id for d in comparison.stage_deltas}
    progress_summary = str(raw.get("progress_summary") or "").strip()
    stage_business_deltas = _parse_stage_deltas(raw.get("stage_business_deltas"), allowed_stage_ids)
    current_risks = _parse_risks(raw.get("risks"))
    next_focus = [s for item in (raw.get("next_focus") or []) if (s := str(item).strip())]

    all_text = " ".join([
        progress_summary, *stage_business_deltas.values(),
        *(r["text"] for r in current_risks), *next_focus,
    ])
    allowed_paths = {
        e.path for model in (from_model, to_model)
        for v in model.verdicts for e in v.verified_evidence
    }
    violations = check_text(all_text, allowed_ids=set(), allowed_paths=allowed_paths)
    if violations:
        reasons = "; ".join(v.render() for v in violations)
        return InterpretedComparison(notes=[f"Бизнес-интерпретация отклонена сторожем: {reasons}"])

    return InterpretedComparison(
        progress_summary=progress_summary,
        stage_business_deltas=stage_business_deltas,
        current_risks=current_risks,
        next_focus=next_focus,
        source="llm",
    )


def _digest(comparison: VersionComparison, from_model: ProgressModel, to_model: ProgressModel) -> dict:
    stages = []
    for delta in comparison.stage_deltas:
        prev = _verdict_for(from_model, delta.canonical_stage_id)
        curr = _verdict_for(to_model, delta.canonical_stage_id)
        stages.append({
            "canonical_stage_id": delta.canonical_stage_id,
            "title": delta.title,
            "change_type": delta.change_type,
            "previous_status": delta.previous_status,
            "current_status": delta.current_status,
            "previous_explanation": prev.explanation if prev else "",
            "current_explanation": curr.explanation if curr else "",
        })
    return {
        "readiness_before": comparison.readiness_before,
        "readiness_after": comparison.readiness_after,
        "stages": stages,
    }


def _verdict_for(model: ProgressModel, canonical_stage_id: str) -> ItemVerdict | None:
    for verdict in model.verdicts:
        if verdict.canonical_stage_id == canonical_stage_id:
            return verdict
    return None


def _parse_stage_deltas(raw: Any, allowed_stage_ids: set[str]) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    result = {}
    for stage_id, text in raw.items():
        text = str(text).strip()
        if str(stage_id) in allowed_stage_ids and text:
            result[str(stage_id)] = text
    return result


def _parse_risks(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    allowed_states = {"NEW", "PERSISTING", "RESOLVED"}
    risks = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        state = str(item.get("state") or "").strip().upper()
        if text and state in allowed_states:
            risks.append({"text": text, "state": state})
    return risks
