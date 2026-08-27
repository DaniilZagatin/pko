"""Единый агент: за одну сессию выделяет пункты плана из презентации и
сопоставляет их с кодом целевого репозитория.

Раньше это были две разные сущности — нетулованная роль `planner` (один
одноразовый вызов, читает готовый текст слайдов, отвечает JSON-списком
пунктов) и агентная роль `matcher` (видит только то, что уже передал planner,
и не может свериться с презентацией). Теперь это один и тот же tool-calling
движок с одной и той же личностью: агенту в первом сообщении приходит текст
всей презентации целиком, а `read_slides` (`progress.agent_tools.ToolBox`)
даёт посмотреть её ещё раз, если по ходу расследования нужно свериться с
формулировкой. Остальные инструменты — `list_files`/`read_file`/`search` — те
же, что и раньше, без ограничения на число вызовов.

Работа идёт по одному пункту плана за раз: агент собирает про пункт evidence
инструментами, затем вызывает `submit_verdict` — и пункт с вердиктом
проверяется кодом (`verify_evidence`/`_guard_explanation`) сразу же, не в
конце сессии. Так агент переходит к следующему пункту, пока не вызовет
`finish`. Это даёт устойчивость к обрыву: если бюджет шагов исчерпан
до `finish`, в отчёт всё равно попадают уже отправленные и проверенные
пункты, а не пропадает вся сессия целиком.

Ни одна evidence-ссылка не публикуется без проверки: `progress.verify.verify_evidence`
подтверждает её структурно (путь и строка существуют на этом коммите, рядом —
слово из основания). Неподтверждённая ссылка не отбрасывается — остаётся в
отчёте с `verified=False` и причиной, а вердикт `DONE`/`PARTIAL` без единой
подтверждённой ссылки явно помечается негrounded (`ItemVerdict.is_grounded`),
а не тихо принимается на слово агента.

Свободный текст `explanation` — отдельная поверхность: `evidence` проверяется
структурно, а прозу можно было опубликовать как есть, с любым выдуманным
именем файла внутри предложения. `report.guard.check_text` проверяет её на
упоминания путей, которых нет в репозитории — нарушение не роняет вердикт,
только заменяет объяснение нейтральной пометкой (`_guard_explanation`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from pko.errors import LlmError
from pko.extractors.base import Tree
from pko.extractors.runner import Extraction
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.agent_tools import TOOL_SCHEMAS as REPO_TOOL_SCHEMAS, ToolBox
from pko.progress.pptx_reader import Slide, render_slide
from pko.progress.schema import EvidenceRef, ItemVerdict, PlanItem, STATUSES, UnclaimedGroup
from pko.progress.verify import verify_evidence
from pko.report.guard import check_text

# Бюджет шагов агента на всю сессию (выделение пунктов плана + сопоставление
# всех пунктов с кодом), не на один пункт: пунктов в плане обычно немного
# (единицы-десятки), но заранее их число неизвестно — агент сам решает, когда
# закончить. Отправная точка для первого живого прогона на GLM, крутится
# снаружи (`run_progress(..., max_steps=...)` / `--max-steps`).
DEFAULT_MAX_STEPS = 60

_AGENT_SYSTEM = (
    "Ты — агент, который в одной сессии разбирает план команды из презентации и "
    "сопоставляет его с фактическим состоянием кода репозитория. Следующим сообщением "
    "придёт текст всех слайдов презентации целиком, построчно (строка — фигуры, стоящие "
    "визуально рядом; что именно означает строка, определяй по тексту, а не по номеру). "
    "Инструментом read_slides можно посмотреть презентацию ещё раз, если понадобится "
    "свериться с формулировкой — это не единственный способ её увидеть, а возможность "
    "перечитать. План описан бизнес-языком (идея, название функциональности), а не "
    "терминами кода — не жди буквальных совпадений, подбирай ключевые слова и синонимы сам.\n"
    "Работай по одному пункту плана за раз: выдели пункт из текста слайдов, инструментами "
    "list_files/read_file/search (можно вызывать сколько угодно раз) выясни, реализован ли "
    "он, затем вызови submit_verdict с описанием пункта и вердиктом по нему сразу. После "
    "этого переходи к следующему пункту. Когда отправил вердикты по всем пунктам — вызови "
    "finish.\n"
    "Не выдумывай пункты, которых нет в тексте слайдов, и не ссылайся на номера слайдов, "
    "которых не было во входе — если сомневаешься в номере, посмотри read_slides ещё раз. "
    "evidence — только реальные путь и строка из того, что ты действительно прочитал "
    "инструментами; basis — короткое основание своими словами, обязательно содержащее "
    "конкретное имя (функции, класса, эндпоинта, файла), которое реально стоит рядом со "
    "строкой — по нему проверяется ссылка. Если подтверждения не нашёл — отправь "
    "NOT_STARTED с пустым evidence, не выдумывай ссылки. Дополнительно укажи progress — свою "
    "оценку процента готовности пункта (0-100): для DONE это 100, для NOT_STARTED — 0, для "
    "PARTIAL/UNCLEAR — своя оценка на глаз.\n"
    "explanation читает руководитель, не разработчик: пиши его бизнес-языком — что по факту "
    "реализовано и в каком состоянии пункт, как это выглядит для пользователя/процесса. Не "
    "упоминай код: ни путей к файлам, ни имён функций/классов/эндпоинтов, ни тестов — это "
    "язык evidence/basis, не explanation. Не ограничивайся пересказом статуса одним "
    "предложением («сделано»/«не начато») — добавь содержательное наблюдение по существу "
    "пункта: чего именно не хватает для PARTIAL/UNCLEAR, в чём риск или ограничение, что "
    "стоит уточнить у команды. За один ход можно вызвать один или несколько инструментов, "
    "включая несколько submit_verdict подряд, если уверен сразу по нескольким пунктам."
)

_SUBMIT_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_verdict",
        "description": "Отправить один пункт плана и вердикт по нему сразу — вызывай один "
                       "раз на каждый обнаруженный пункт, сразу после того как собрал по "
                       "нему evidence. Повторный вызов с тем же item_id заменяет предыдущий "
                       "вердикт по этому пункту.",
        "parameters": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "description": "короткий устойчивый идентификатор латиницей/цифрами/дефисами"},
                "title": {"type": "string", "description": "название пункта плана"},
                "stage": {"type": "string", "description": "этап/спринт, если указан на слайде"},
                "description": {"type": "string", "description": "краткое описание пункта своими словами"},
                "source_slide": {"type": "integer", "description": "номер слайда, с которого взят пункт — только реально существующий во входе"},
                "status": {"type": "string", "enum": list(STATUSES)},
                "explanation": {
                    "type": "string",
                    "description": "бизнес-объяснение вердикта для руководителя: что реализовано и в "
                                   "каком состоянии пункт, плюс содержательный комментарий по существу "
                                   "(риск/ограничение/чего не хватает) — без путей к файлам, имён "
                                   "функций/классов/эндпоинтов и упоминаний тестов",
                },
                "progress": {"type": "integer", "description": "оценка процента готовности пункта, 0-100"},
                "evidence": {
                    "type": "array",
                    "description": "ссылки на код, подтверждающие вердикт; пусто, если статус NOT_STARTED",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "line": {"type": "integer"},
                            "basis": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["item_id", "title", "source_slide", "status", "explanation"],
        },
    },
}

_FINISH_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "Вызови, когда отправил вердикты по всем пунктам плана — сессия завершена.",
        "parameters": {"type": "object", "properties": {}},
    },
}

_SESSION_TOOL_SCHEMAS: list[dict[str, Any]] = REPO_TOOL_SCHEMAS + [_SUBMIT_VERDICT_SCHEMA, _FINISH_SCHEMA]
_CONTROL_TOOLS = ("submit_verdict", "finish")

_MAX_MALFORMED = 3
_REPEAT_STOP = 3


@dataclass
class AgentResult:
    items: list[PlanItem] = field(default_factory=list)
    verdicts: list[ItemVerdict] = field(default_factory=list)
    source: str = "none"
    notes: list[str] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return bool(self.items)


def run_agent(
    slides: list[Slide],
    tree: Tree,
    spec: ModelSpec | None,
    client: ChatClient | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    on_verdict: Callable[[PlanItem, ItemVerdict], None] | None = None,
) -> AgentResult:
    """Выделить пункты плана из слайдов и сопоставить их с кодом — одна сессия.

    `client` — тестовая инъекция по образцу остальных LLM-ролей: без неё
    `ChatClient` по умолчанию читает/пишет реальный `~/.pko/llm-cache`.

    `on_verdict` — необязательный колбэк для live-прогресса (веб-эндпоинт):
    вызывается сразу после того, как очередной `submit_verdict` принят (не на
    отклонённый). Без колбэка (`None`) поведение не меняется.
    """
    if spec is None:
        return AgentResult(notes=["Matcher не настроен: план и вердикты не получены"])

    content_slides = [s for s in slides if not s.is_empty]
    if not content_slides:
        return AgentResult(notes=["В презентации нет текстовых фигур"])

    known_slides = {s.number for s in content_slides}
    user = "Слайды презентации:\n\n" + "\n\n".join(render_slide(s) for s in content_slides)

    chat_client = client if client is not None else ChatClient(spec=spec)
    items, verdicts, notes = _run_session(
        user, known_slides, content_slides, tree, chat_client, max_steps, on_verdict=on_verdict
    )

    ungrounded = sum(1 for v in verdicts if v.status in ("DONE", "PARTIAL") and not v.is_grounded)
    if ungrounded:
        notes.append(
            f"Вердиктов DONE/PARTIAL без единой подтверждённой ссылки на код: {ungrounded} — "
            "агент утверждает прогресс, но код это не показывает; нужна ручная проверка"
        )
    return AgentResult(items=items, verdicts=verdicts, source="llm" if items else "none", notes=notes)


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


def _run_session(
    user_brief: str,
    known_slides: set[int],
    slides: list[Slide],
    tree: Tree,
    chat_client: ChatClient,
    max_steps: int,
    on_verdict: Callable[[PlanItem, ItemVerdict], None] | None = None,
) -> tuple[list[PlanItem], list[ItemVerdict], list[str]]:
    """Цикл «пункт → evidence → submit_verdict → следующий пункт» до `finish`."""
    tools = ToolBox(tree, slides)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _AGENT_SYSTEM},
        {"role": "user", "content": user_brief},
    ]
    notes: list[str] = []
    recent_calls: list[tuple[tuple[str, str], ...]] = []
    malformed = 0
    items_by_id: dict[str, PlanItem] = {}
    verdicts_by_id: dict[str, ItemVerdict] = {}
    finished = False

    for step in range(1, max(1, max_steps) + 1):
        try:
            result = chat_client.chat(messages, tools=_SESSION_TOOL_SCHEMAS, max_tokens=2000)
        except LlmError as exc:
            notes.append(f"агент недоступен на шаге {step}: {exc.message}")
            break

        if not result.tool_calls:
            malformed += 1
            if malformed >= _MAX_MALFORMED:
                notes.append(f"агент не вызвал инструмент {malformed} раз подряд — остановлено")
                break
            messages.append({"role": "assistant", "content": result.text})
            messages.append({
                "role": "user",
                "content": "Нужно вызвать инструмент — включая submit_verdict по пункту "
                           "плана или finish, когда закончил.",
            })
            continue
        malformed = 0

        signature = tuple(sorted(
            (str((c.get("function") or {}).get("name") or ""),
             str((c.get("function") or {}).get("arguments") or ""))
            for c in result.tool_calls
        ))
        recent_calls.append(signature)
        if len(recent_calls) >= _REPEAT_STOP and len(set(recent_calls[-_REPEAT_STOP:])) == 1:
            notes.append(f"остановлено — {_REPEAT_STOP} одинаковых вызова подряд")
            break

        messages.append({
            "role": "assistant",
            "content": result.text or None,
            "tool_calls": result.tool_calls,
        })

        for call in result.tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            raw_args = function.get("arguments") or "{}"
            call_id = str(call.get("id") or "")
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                messages.append({
                    "role": "tool", "tool_call_id": call_id,
                    "content": f"аргументы инструмента не являются валидным JSON: {raw_args!r}",
                })
                continue
            args = args if isinstance(args, dict) else {}

            if name == "submit_verdict":
                item, verdict, error = _submit_verdict(args, known_slides, set(items_by_id), tree)
                if error:
                    tool_content = f"вердикт отклонён: {error}"
                else:
                    items_by_id[item.id] = item
                    verdicts_by_id[item.id] = verdict
                    tool_content = "вердикт принят"
                    if on_verdict is not None:
                        on_verdict(item, verdict)
                messages.append({"role": "tool", "tool_call_id": call_id, "content": tool_content})
                continue

            if name == "finish":
                messages.append({"role": "tool", "tool_call_id": call_id, "content": "сессия завершена"})
                finished = True
                continue

            outcome = tools.call(name, args)
            messages.append({"role": "tool", "tool_call_id": call_id, "content": outcome.content})

        if finished:
            break
    else:
        notes.append(f"бюджет шагов исчерпан ({max_steps})")

    if not verdicts_by_id:
        notes.append("Пункты плана не отправлены (submit_verdict не вызывался)")
    return list(items_by_id.values()), list(verdicts_by_id.values()), notes


def _submit_verdict(
    args: dict, known_slides: set[int], seen_ids: set[str], tree: Tree
) -> tuple[PlanItem | None, ItemVerdict | None, str | None]:
    item = _validate_item(args, known_slides, seen_ids)
    if item is None:
        return None, None, "проверьте title и source_slide (должен быть реальным номером слайда из входных данных)"
    verdict = _parse_final(item, args, tree)
    return item, verdict, None


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
    # Явный id — как есть, даже если уже встречался: это намеренный ресабмит
    # (агент пересматривает уже отправленный пункт), не коллизия. Дизамбигуация
    # нужна только автосгенерированному id — тут коллизия означает, что две
    # РАЗНЫЕ заявки с одного слайда обе остались без id.
    explicit_id = str(raw.get("id") or raw.get("item_id") or "").strip()
    if explicit_id:
        item_id = explicit_id
    else:
        item_id = f"slide-{source_slide}-{len(seen_ids) + 1}"
        if item_id in seen_ids:
            item_id = f"{item_id}-{len(seen_ids) + 1}"
    return PlanItem(
        id=item_id,
        title=title,
        stage=str(raw.get("stage") or "").strip(),
        description=str(raw.get("description") or "").strip(),
        source_slide=source_slide,
    )


def _unclear(item: PlanItem, reason: str) -> ItemVerdict:
    return ItemVerdict(item_id=item.id, status="UNCLEAR", explanation=reason, evidence=[])


def _parse_final(item: PlanItem, raw: object, tree: Tree) -> ItemVerdict:
    if not isinstance(raw, dict):
        return _unclear(item, "вердикт не является объектом")
    status = str(raw.get("status") or "").strip().upper()
    if status not in STATUSES:
        status = "UNCLEAR"
    explanation = str(raw.get("explanation") or "").strip()
    try:
        progress = int(raw.get("progress"))
    except (TypeError, ValueError):
        progress = 0

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

    verdict = ItemVerdict(item_id=item.id, status=status, explanation=explanation,
                          evidence=evidence, progress=progress)
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
