"""Роль reporter: связный текстовый вывод по итогам сверки всего плана.

По остальным ролям (`planner`, `matcher`) модель судит по одному слайду или
одному пункту — здесь, наоборот, единственный запрос по итогам **всех**
пунктов сразу: не новое расследование, а обобщение уже готовых, проверенных
вердиктов в понятный человеку абзац.

Роль необязательна: без настроенного `reporter` отчёт собирается как обычно,
просто без сводного вывода — в отличие от `planner`/`matcher`, без которых
отчёта нет вообще, здесь деградация не молчаливая (причина всегда попадает в
`notes`), но и не блокирующая.

Текст проверяется тем же `report.guard.check_text`, что и объяснения matcher'а
(`progress.matcher._guard_explanation`) — упоминание пути, которого нет среди
подтверждённых evidence, отклоняет весь вывод целиком (не подменяется тихо
шаблоном: молчаливая подмена авторства недопустима, читатель должен либо
видеть текст модели, либо явно знать, что его нет).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pko.errors import LlmError
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.schema import ProgressModel
from pko.report.guard import check_text

_SYSTEM = (
    "Ты пишешь краткий связный вывод по итогам сверки плана команды с кодом репозитория. "
    "Тебе даны вердикты по всем пунктам плана (статус, обоснование, подтверждённые ссылки на "
    "код) и список кода, не относящегося ни к одному пункту плана. Напиши 3-6 предложений на "
    "русском простым текстом (без JSON, без списков, без заголовков): как обстоят дела в "
    "целом, что сделано уверенно, что вызывает сомнения или осталось не начато, на что стоит "
    "обратить внимание в первую очередь. Используй только пункты плана и пути к файлам, "
    "которые даны тебе явно в данных ниже — не выдумывай ничего сверх этого."
)


@dataclass
class WrittenSummary:
    text: str = ""
    source: str = "none"
    notes: list[str] = field(default_factory=list)


def summarize_progress(
    model: ProgressModel, spec: ModelSpec | None, client: ChatClient | None = None
) -> WrittenSummary:
    """Сформировать сводный вывод. Без роли/при сбое — пустой результат, не шаблон.

    `client` — та же тестовая инъекция, что и у `extract_plan`/`match_plan`:
    без неё `ChatClient` по умолчанию читает/пишет реальный `~/.pko/llm-cache`.
    """
    if spec is None:
        return WrittenSummary(notes=["Сводный вывод не сформирован: роль reporter не настроена"])
    if not model.verdicts:
        return WrittenSummary(notes=["Сводный вывод не сформирован: нет вердиктов по пунктам плана"])

    user = "Вердикты по пунктам плана:\n\n" + json.dumps(_digest(model), ensure_ascii=False, indent=2)

    chat_client = client if client is not None else ChatClient(spec=spec)
    try:
        text = chat_client.complete(system=_SYSTEM, user=user, max_tokens=1000)
    except LlmError as exc:
        return WrittenSummary(notes=[f"Сводный вывод не сформирован: reporter недоступен: {exc.message}"])

    allowed_paths = {e.path for v in model.verdicts for e in v.verified_evidence}
    violations = check_text(text, allowed_ids=set(model.items), allowed_paths=allowed_paths)
    if violations:
        reasons = "; ".join(v.render() for v in violations)
        return WrittenSummary(notes=[f"Сводный вывод отклонён сторожем: {reasons}"])

    return WrittenSummary(text=text.strip(), source="llm")


def _digest(model: ProgressModel) -> dict:
    verdicts = []
    for v in model.verdicts:
        item = model.items.get(v.item_id)
        verdicts.append({
            "id": v.item_id,
            "title": item.title if item else v.item_id,
            "status": v.status,
            "explanation": v.explanation,
            "evidence_paths": [e.path for e in v.verified_evidence],
        })
    return {
        "verdicts": verdicts,
        "possibly_extra_work": [
            {"group": g.group, "example_paths": g.example_paths} for g in model.unclaimed
        ],
    }
