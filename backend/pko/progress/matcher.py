"""Роль matcher: агент, который сам ищет по коду подтверждение пункту плана.

Презентации описывают бизнес-идеи и названия функциональности, а не код —
заранее собранный список кандидатов (детерминированные факты экстракторов)
почти никогда лексически не пересекается с этим языком. Поэтому здесь не
единый batch-запрос по готовому списку, а отдельный агентный цикл на каждый
пункт плана: модель сама решает, что посмотреть, через read-only инструменты
`progress.agent_tools.ToolBox` (`list_files`/`read_file`/`search`), а не
разбирает то, что нашли экстракторы.

Ни одна evidence-ссылка не публикуется без проверки: `progress.verify.verify_evidence`
подтверждает её структурно (путь и строка существуют на этом коммите, рядом —
слово из основания). Неподтверждённая ссылка не отбрасывается — остаётся в
отчёте с `verified=False` и причиной, а вердикт `DONE`/`PARTIAL` без единой
подтверждённой ссылки явно помечается негрунтованным (`ItemVerdict.is_grounded`),
а не тихо принимается на слово агента.

Свободный текст `explanation` — отдельная поверхность: `evidence` проверяется
структурно, а прозу можно было опубликовать как есть, с любым выдуманным
именем файла внутри предложения. `report.guard.check_text` проверяет её на
упоминания путей, которых нет в репозитории — нарушение не роняет вердикт,
только заменяет объяснение нейтральной пометкой (`_guard_explanation`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pko.errors import LlmError
from pko.extractors.base import Tree
from pko.extractors.runner import Extraction
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.agent_tools import TOOL_SCHEMAS, ToolBox
from pko.progress.schema import EvidenceRef, ItemVerdict, PlanItem, STATUSES, UnclaimedGroup
from pko.progress.verify import verify_evidence
from pko.report.guard import check_text

# Бюджет шагов агента на один пункт плана. Пунктов в плане обычно немного
# (единицы-десятки), но при N пунктах это до N×DEFAULT_MAX_STEPS вызовов LLM
# за прогон — сознательный компромисс между полнотой поиска и стоимостью,
# настраиваемый снаружи (`run_progress(..., max_steps=...)`).
DEFAULT_MAX_STEPS = 30

_AGENT_SYSTEM = (
    "Ты — агент, который проверяет, реализован ли в репозитории конкретный пункт плана "
    "команды. План описан бизнес-языком (идея, название функциональности), а не терминами "
    "кода — не жди буквальных совпадений, подбирай ключевые слова и синонимы сам. "
    "У тебя есть инструменты, только на чтение, по коду репозитория на конкретном коммите — "
    "используй их через tool calling. Исследуй репозиторий и, когда достаточно уверен, дай "
    "финальный ответ обычным текстом (не вызовом инструмента) — один JSON-объект: "
    '{"status": "DONE"|"PARTIAL"|"NOT_STARTED"|"UNCLEAR", "explanation": "...", '
    '"evidence": [{"path": "...", "line": N, "basis": "..."}]}. '
    "evidence — только реальные путь и строка из того, что ты действительно прочитал "
    "инструментами; basis — короткое основание своими словами, обязательно содержащее "
    "конкретное имя (функции, класса, эндпоинта, файла), которое реально стоит рядом со "
    "строкой — по нему проверяется ссылка. Если подтверждения не нашёл — верни NOT_STARTED "
    "с пустым evidence, не выдумывай ссылки. За один ход — либо вызов инструмента, либо "
    "финальный текстовый ответ, не оба сразу."
)

_JSON_OBJECT = re.compile(r"\{[\s\S]*\}")
_MAX_MALFORMED = 3
_REPEAT_STOP = 3


@dataclass
class MatchResult:
    verdicts: list[ItemVerdict] = field(default_factory=list)
    source: str = "none"
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.verdicts)


def match_plan(
    items: list[PlanItem],
    tree: Tree,
    spec: ModelSpec | None,
    client: ChatClient | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> MatchResult:
    """Сопоставить пункты плана с кодом — отдельный агентный цикл на каждый пункт.

    `client` — тестовая инъекция по образцу `pko.progress.plan_extract.extract_plan`:
    без неё `ChatClient` по умолчанию читает/пишет реальный `~/.pko/llm-cache`.
    """
    if spec is None:
        return MatchResult(notes=["Matcher не настроен: сопоставление не выполнено"])
    if not items:
        return MatchResult(notes=["Пунктов плана нет — сопоставлять нечего"])

    verdicts: list[ItemVerdict] = []
    notes: list[str] = []
    ungrounded = 0
    for item in items:
        verdict, item_notes = _investigate_item(item, tree, spec, client, max_steps)
        verdicts.append(verdict)
        notes.extend(item_notes)
        if verdict.status in ("DONE", "PARTIAL") and not verdict.is_grounded:
            ungrounded += 1

    if ungrounded:
        notes.append(
            f"Вердиктов DONE/PARTIAL без единой подтверждённой ссылки на код: {ungrounded} — "
            "агент утверждает прогресс, но код это не показывает; нужна ручная проверка"
        )
    return MatchResult(verdicts=verdicts, source="llm", notes=notes)


def find_unclaimed_paths(
    extraction: Extraction, verdicts: list[ItemVerdict], limit: int = 20
) -> list[UnclaimedGroup]:
    """Файлы-кандидаты, не процитированные ни одной подтверждённой evidence."""
    claimed = {
        e.path for v in verdicts for e in v.verified_evidence if e.path
    }
    candidate_paths = sorted({f.path for f in extraction.facts if f.path})
    unclaimed = [p for p in candidate_paths if p not in claimed]

    groups: dict[str, list[str]] = {}
    for path in unclaimed:
        segments = path.split("/")
        key = "/".join(segments[:2]) if len(segments) > 1 else segments[0]
        groups.setdefault(key, []).append(path)

    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))[:limit]
    return [
        UnclaimedGroup(group=key, example_paths=paths[:3], file_count=len(paths))
        for key, paths in ranked
    ]


def _investigate_item(
    item: PlanItem,
    tree: Tree,
    spec: ModelSpec,
    client: ChatClient | None,
    max_steps: int,
) -> tuple[ItemVerdict, list[str]]:
    """Изолированный агентный цикл на один пункт плана.

    Возвращает вердикт и заметки о ходе расследования (пустые при чистом
    завершении через `final`).
    """
    chat_client = client if client is not None else ChatClient(spec=spec)
    tools = ToolBox(tree)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _AGENT_SYSTEM},
        {"role": "user", "content": _item_brief(item)},
    ]
    notes: list[str] = []
    recent_calls: list[tuple[tuple[str, str], ...]] = []
    malformed = 0

    for step in range(1, max(1, max_steps) + 1):
        try:
            result = chat_client.chat(messages, tools=TOOL_SCHEMAS, max_tokens=1500)
        except LlmError as exc:
            notes.append(f"{item.id}: matcher недоступен на шаге {step}: {exc.message}")
            return _unclear(item, "LLM недоступен во время исследования"), notes

        if result.tool_calls:
            signature = tuple(sorted(
                (str((c.get("function") or {}).get("name") or ""),
                 str((c.get("function") or {}).get("arguments") or ""))
                for c in result.tool_calls
            ))
            recent_calls.append(signature)
            if len(recent_calls) >= _REPEAT_STOP and len(set(recent_calls[-_REPEAT_STOP:])) == 1:
                notes.append(f"{item.id}: остановлено — {_REPEAT_STOP} одинаковых вызова инструмента подряд")
                return _unclear(item, f"агент повторил один и тот же вызов {_REPEAT_STOP} раза подряд"), notes

            messages.append({
                "role": "assistant",
                "content": result.text or None,
                "tool_calls": result.tool_calls,
            })
            for call in result.tool_calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                raw_args = function.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    tool_content = f"аргументы инструмента не являются валидным JSON: {raw_args!r}"
                else:
                    outcome = tools.call(name, args if isinstance(args, dict) else {})
                    tool_content = outcome.content
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(call.get("id") or ""),
                    "content": tool_content,
                })
            continue

        final_obj = _extract_json_object(result.text)
        if final_obj is None:
            malformed += 1
            if malformed >= _MAX_MALFORMED:
                notes.append(f"{item.id}: неразбираемый ответ агента {malformed} раз подряд — остановлено")
                return _unclear(item, "агент не вернул распознаваемый JSON"), notes
            messages.append({"role": "assistant", "content": result.text})
            messages.append({
                "role": "user",
                "content": "Финальный ответ должен быть одним JSON-объектом: "
                           "{status, explanation, evidence}. Если хочешь продолжить поиск — вызови инструмент.",
            })
            continue

        return _parse_final(item, final_obj, tree), notes

    notes.append(f"{item.id}: бюджет шагов исчерпан ({max_steps}) без финального ответа")
    return _unclear(item, f"бюджет шагов исчерпан ({max_steps}) — расследование не завершено"), notes


def _item_brief(item: PlanItem) -> str:
    parts = [f"Пункт плана: {item.title}"]
    if item.stage:
        parts.append(f"Этап: {item.stage}")
    if item.description:
        parts.append(f"Описание: {item.description}")
    return "\n".join(parts)


def _unclear(item: PlanItem, reason: str) -> ItemVerdict:
    return ItemVerdict(item_id=item.id, status="UNCLEAR", explanation=reason, evidence=[])


def _extract_json_object(raw: str) -> dict | None:
    match = _JSON_OBJECT.search(raw or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_final(item: PlanItem, raw: object, tree: Tree) -> ItemVerdict:
    if not isinstance(raw, dict):
        return _unclear(item, "final не является объектом")
    status = str(raw.get("status") or "").strip().upper()
    if status not in STATUSES:
        status = "UNCLEAR"
    explanation = str(raw.get("explanation") or "").strip()

    evidence: list[EvidenceRef] = []
    for raw_ev in raw.get("evidence") or []:
        if not isinstance(raw_ev, dict):
            continue
        path = str(raw_ev.get("path") or "").strip()
        basis = str(raw_ev.get("basis") or "").strip()
        try:
            line = int(raw_ev["line"]) if raw_ev.get("line") is not None else None
        except (TypeError, ValueError):
            line = None
        if not path:
            continue
        result = verify_evidence(path, line, basis, tree)
        evidence.append(EvidenceRef(
            path=result.path, line=result.line, basis=basis,
            verified=result.ok, reason=result.reason,
        ))

    verdict = ItemVerdict(item_id=item.id, status=status, explanation=explanation, evidence=evidence)
    _guard_explanation(verdict, tree)
    return verdict


def _guard_explanation(verdict: ItemVerdict, tree: Tree) -> bool:
    """Отклонить объяснение агента, если оно упоминает путь вне репозитория.

    Возвращает `True`, если объяснение заменено. `evidence` уже проверена
    отдельно (`verify_evidence`) — эта проверка про свободный текст, который
    иначе публиковался бы как есть с любым выдуманным именем файла внутри
    предложения. `allowed_ids=set()` — намеренно: `ID_PATTERN` в
    `report.guard` заточен под ID-схему Gate (`BBB-`/`AO-`/`NEED-`…), а не под
    произвольные `item_id` пунктов плана, поэтому ссылку на несуществующий
    пункт плана в прозе этот guard не ловит — только пути к файлам.
    """
    if not verdict.explanation:
        return False
    violations = check_text(verdict.explanation, allowed_ids=set(), allowed_paths=set(tree.files))
    if not violations:
        return False
    reasons = "; ".join(v.render() for v in violations)
    verdict.explanation = f"(объяснение отклонено сторожем: {reasons})"
    return True
