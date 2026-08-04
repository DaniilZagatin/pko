"""Роль сборщика (GLM): группировка кандидатов в переиспользуемые блоки.

Модель видит только список кандидатов — идентификатор, тип, имя, пакет, путь.
Ни кода, ни промптов, ни содержимого конфигураций. Ответ обязан быть JSON и
ссылаться на существующие идентификаторы: всё остальное отбрасывается кодом.
Если endpoint не задан или ответ не прошёл проверку, сборка идёт
детерминированным путём — по пакетам.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pko.assemble.candidates import Candidate
from pko.errors import LlmError
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec

_SYSTEM = (
    "Ты группируешь технические элементы кода в переиспользуемые бизнес-блоки (BBB). "
    "Отвечай строго одним JSON-объектом вида "
    '{\"groups\": [{\"name\": \"...\", \"candidate_ids\": [\"...\"]}]}. '
    "Используй только переданные candidate_id, не придумывай новые. Название блока — "
    "короткая русская формулировка бизнес-действия. Не добавляй пояснений вне JSON."
)

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")


@dataclass
class GroupingResult:
    groups: dict[str, list[str]] = field(default_factory=dict)
    source: str = "template"
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.groups)


def propose_bbb_groups(candidates: list[Candidate], spec: ModelSpec | None) -> GroupingResult:
    """Предложить группировку кандидатов. Пустой результат — сборка идёт по пакетам."""
    if spec is None:
        return GroupingResult(notes=["Сборщик не настроен: группировка по пакетам"])

    capabilities = [c for c in candidates if c.type == "CAPABILITY"]
    if not capabilities:
        return GroupingResult(notes=["Нет кандидатов для группировки"])

    payload = [c.to_prompt_dict() for c in capabilities][:400]
    user = (
        "Сгруппируй элементы в 4–12 бизнес-блоков. Каждый элемент попадает ровно в одну "
        "группу; элементы, смысл которых не ясен, объединяй по пакету.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    try:
        raw = ChatClient(spec=spec).complete(system=_SYSTEM, user=user, max_tokens=3000)
    except LlmError as exc:
        return GroupingResult(notes=[f"Сборщик недоступен, группировка по пакетам: {exc.message}"])

    parsed = _parse(raw)
    if parsed is None:
        return GroupingResult(notes=["Ответ сборщика не является JSON — группировка по пакетам"])

    known = {c.id for c in capabilities}
    groups: dict[str, list[str]] = {}
    dropped: list[str] = []
    for group in parsed:
        name = str(group.get("name") or "").strip()
        ids = [str(i) for i in (group.get("candidate_ids") or [])]
        valid = [i for i in ids if i in known]
        dropped.extend(i for i in ids if i not in known)
        if name and valid:
            groups.setdefault(name, []).extend(valid)

    notes: list[str] = []
    if dropped:
        notes.append(
            f"Сборщик сослался на {len(dropped)} несуществующих кандидатов — они отброшены"
        )
    missed = known - {i for ids in groups.values() for i in ids}
    if missed:
        notes.append(f"Кандидатов вне групп: {len(missed)} — добавлены по пакету")

    if not groups:
        return GroupingResult(notes=notes + ["Годных групп не получено — группировка по пакетам"])
    return GroupingResult(groups=groups, source="llm", notes=notes)


def _parse(raw: str) -> list[dict] | None:
    match = _JSON_BLOCK.search(raw or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    groups = data.get("groups") if isinstance(data, dict) else None
    return groups if isinstance(groups, list) else None
