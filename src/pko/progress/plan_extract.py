"""Роль planner: текст слайдов → структурированный список пунктов плана.

По образцу `pko.assemble.llm_map.propose_bbb_groups`: ответ обязан быть одним
JSON-объектом и ссылаться только на реально переданные номера слайдов — всё
остальное отбрасывается, а не подставляется по умолчанию. Модель здесь не
видит код целевого репозитория, только текст презентации. Без endpoint'а или
при сбое возвращается пустой результат с причиной в `notes` — это уходит в
`gaps` итогового отчёта, а не проваливается молча.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pko.errors import LlmError
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.pptx_reader import Slide
from pko.progress.schema import PlanItem

_SYSTEM = (
    "Ты читаешь текст слайдов презентации с планом работы команды и превращаешь его "
    "в список пунктов плана. Отвечай строго одним JSON-объектом вида "
    '{"items": [{"id": "...", "title": "...", "stage": "...", '
    '"description": "...", "source_slide": N}]}. '
    "source_slide — номер слайда из входных данных, с которого взят пункт; не "
    "придумывай номера, которых не было во входе. id — короткий устойчивый "
    "идентификатор латиницей/цифрами/дефисами. Не добавляй пояснений вне JSON, не "
    "выдумывай пункты, которых нет в тексте слайдов."
)

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


@dataclass
class PlanExtractionResult:
    items: list[PlanItem] = field(default_factory=list)
    source: str = "none"
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.items)


def extract_plan(
    slides: list[Slide], spec: ModelSpec | None, client: ChatClient | None = None
) -> PlanExtractionResult:
    """Извлечь пункты плана из слайдов. Без endpoint'а/при сбое — пустой результат.

    `client` — по образцу `pko.agent.loop.run_scout`: тесты подставляют свой
    экземпляр (`use_cache=False`), чтобы не писать в реальный `~/.pko/llm-cache`
    и не читать чужой закешированный ответ по совпавшему payload.
    """
    if spec is None:
        return PlanExtractionResult(notes=["Planner не настроен: план не извлечён"])

    content_slides = [s for s in slides if not s.is_empty]
    if not content_slides:
        return PlanExtractionResult(notes=["В презентации нет текстовых фигур"])

    known_slides = {s.number for s in content_slides}
    payload = [_slide_payload(s) for s in content_slides]
    user = (
        "Слайды презентации (номер, заголовок, фигуры с текстом, координатами в "
        "дюймах и предполагаемой раскладкой):\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    chat_client = client if client is not None else ChatClient(spec=spec)
    try:
        raw = chat_client.complete(system=_SYSTEM, user=user, max_tokens=4000)
    except LlmError as exc:
        return PlanExtractionResult(notes=[f"Planner недоступен: {exc.message}"])

    parsed = _parse(raw)
    if parsed is None:
        return PlanExtractionResult(notes=["Ответ planner не является JSON — план не извлечён"])

    items: list[PlanItem] = []
    seen_ids: set[str] = set()
    dropped = 0
    for raw_item in parsed:
        item = _validate_item(raw_item, known_slides, seen_ids)
        if item is None:
            dropped += 1
            continue
        seen_ids.add(item.id)
        items.append(item)

    notes: list[str] = []
    if dropped:
        notes.append(f"Отброшено пунктов с некорректными полями или номером слайда: {dropped}")
    if not items:
        return PlanExtractionResult(notes=notes + ["Годных пунктов плана не получено"])
    return PlanExtractionResult(items=items, source="llm", notes=notes)


def _slide_payload(slide: Slide) -> dict:
    return {
        "slide": slide.number,
        "heading": slide.heading,
        "layout_guess": slide.layout.kind,
        "shapes": [{"text": s.text, "left": s.left, "top": s.top} for s in slide.shapes],
    }


def _validate_item(raw: object, known_slides: set[int], seen_ids: set[str]) -> PlanItem | None:
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    try:
        source_slide = int(raw.get("source_slide"))
    except (TypeError, ValueError):
        return None
    if source_slide not in known_slides:
        return None
    item_id = str(raw.get("id") or "").strip() or f"slide-{source_slide}-{len(seen_ids) + 1}"
    if item_id in seen_ids:
        item_id = f"{item_id}-{len(seen_ids) + 1}"
    return PlanItem(
        id=item_id,
        title=title,
        stage=str(raw.get("stage") or "").strip(),
        description=str(raw.get("description") or "").strip(),
        source_slide=source_slide,
    )


def _parse(raw: str) -> list[dict] | None:
    match = _JSON_BLOCK.search(raw or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    items = data.get("items") if isinstance(data, dict) else None
    return items if isinstance(items, list) else None
