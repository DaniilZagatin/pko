"""Роль matcher: для каждого пункта плана — вердикт и evidence-кандидаты по коду.

По образцу `pko.assemble.llm_map`/`pko.progress.plan_extract`: один JSON-вызов
на весь план сразу, ответ обязан ссылаться на реально переданные `item_id` —
всё остальное отбрасывается. Модель здесь не видит текст кода, только путь и
короткое основание факта (`Fact.basis`) — тот же принцип, что и в остальном
PKO: код не публикуется и не уходит во внешние вызовы дальше необходимого.

Ни одна evidence-ссылка не публикуется без проверки: `progress.verify.verify_evidence`
подтверждает её структурно (путь и строка существуют на этом коммите, рядом —
слово из основания). Неподтверждённая ссылка не отбрасывается — остаётся в
отчёте с `verified=False` и причиной, а вердикт `DONE`/`PARTIAL` без единой
подтверждённой ссылки явно помечается негрунтованным (`ItemVerdict.is_grounded`),
а не тихо принимается на слово модели.

Свободный текст `explanation` — отдельная поверхность: `evidence` проверяется
структурно, а прозу можно было опубликовать как есть, с любым выдуманным
именем файла внутри предложения. `report.guard.check_text` (тот же анти-
галлюцинационный guard, что и у писателя в исходном PKO) проверяет её на
упоминания путей, которых нет в репозитории — нарушение не роняет вердикт,
только заменяет объяснение нейтральной пометкой (`_guard_explanation`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pko.errors import LlmError
from pko.extractors.base import Tree
from pko.extractors.runner import Extraction
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.schema import EvidenceRef, ItemVerdict, PlanItem, STATUSES, UnclaimedGroup
from pko.progress.verify import verify_evidence
from pko.report.guard import check_text

_SYSTEM = (
    "Ты сравниваешь пункты плана команды с кандидатами кода из целевого репозитория "
    "и оцениваешь прогресс. Для каждого пункта плана верни вердикт статуса "
    '(один из "DONE", "PARTIAL", "NOT_STARTED", "UNCLEAR") и, если статус не '
    '"NOT_STARTED", список конкретных ссылок на код (путь и строка из переданных '
    "кандидатов), которые это подтверждают. Отвечай строго одним JSON-объектом вида "
    '{"verdicts": [{"item_id": "...", "status": "...", "explanation": "...", '
    '"evidence": [{"path": "...", "line": N, "basis": "..."}]}]}. '
    "Используй только переданные item_id и только пути/строки из переданных "
    "кандидатов кода. basis — короткое основание своими словами, обязательно "
    "содержащее конкретное имя (функции, класса, эндпоинта, файла), которое "
    "реально стоит рядом с указанной строкой — по нему проверяется ссылка. Если "
    "подтверждающего кода нет — верни NOT_STARTED с пустым evidence, не выдумывай "
    "ссылки. Не добавляй пояснений вне JSON."
)

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")

# Столько уникальных путей-кандидатов передаём модели за один вызов — тот же
# порядок величины, что и candidate-cap в assemble/llm_map.py.
MAX_CANDIDATES = 400
# Сколько кратких оснований факта показываем на один путь, чтобы не раздувать
# промпт файлами с сотнями находок.
MAX_BASES_PER_PATH = 4


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
    extraction: Extraction,
    tree: Tree,
    spec: ModelSpec | None,
    client: ChatClient | None = None,
) -> MatchResult:
    """Сопоставить пункты плана с кодом. Без endpoint'а/при сбое — пустой результат.

    `client` — тестовая инъекция по образцу `pko.progress.plan_extract.extract_plan`
    и `pko.agent.loop.run_scout`: без неё `complete()` читает/пишет реальный
    `~/.pko/llm-cache`.
    """
    if spec is None:
        return MatchResult(notes=["Matcher не настроен: сопоставление не выполнено"])
    if not items:
        return MatchResult(notes=["Пунктов плана нет — сопоставлять нечего"])

    candidates = _candidate_paths(extraction)
    if not candidates:
        return MatchResult(notes=["В целевом репозитории нет кандидатов кода"])

    known_ids = {item.id for item in items}
    user = (
        "Пункты плана:\n" + json.dumps([i.to_dict() for i in items], ensure_ascii=False, indent=2)
        + "\n\nКандидаты кода (путь и краткие основания найденных фактов):\n"
        + json.dumps(candidates, ensure_ascii=False, indent=2)
    )

    chat_client = client if client is not None else ChatClient(spec=spec)
    try:
        raw = chat_client.complete(system=_SYSTEM, user=user, max_tokens=6000)
    except LlmError as exc:
        return MatchResult(notes=[f"Matcher недоступен: {exc.message}"])

    parsed = _parse(raw)
    if parsed is None:
        return MatchResult(notes=["Ответ matcher не является JSON — сопоставление не выполнено"])

    verdicts: list[ItemVerdict] = []
    dropped = 0
    ungrounded = 0
    rejected_explanations = 0
    seen_ids: set[str] = set()
    for raw_verdict in parsed:
        verdict = _validate_verdict(raw_verdict, known_ids, seen_ids, tree)
        if verdict is None:
            dropped += 1
            continue
        seen_ids.add(verdict.item_id)
        if _guard_explanation(verdict, tree):
            rejected_explanations += 1
        verdicts.append(verdict)
        if verdict.status in ("DONE", "PARTIAL") and not verdict.is_grounded:
            ungrounded += 1

    missing = known_ids - seen_ids
    for item_id in sorted(missing):
        verdicts.append(ItemVerdict(
            item_id=item_id, status="UNCLEAR",
            explanation="Matcher не вернул вердикт по этому пункту плана.",
        ))

    notes: list[str] = []
    if dropped:
        notes.append(f"Отброшено вердиктов с некорректными полями: {dropped}")
    if missing:
        notes.append(f"Пунктов без вердикта от matcher (помечены UNCLEAR): {len(missing)}")
    if ungrounded:
        notes.append(
            f"Вердиктов DONE/PARTIAL без единой подтверждённой ссылки на код: {ungrounded} — "
            "модель утверждает прогресс, но код это не показывает; нужна ручная проверка"
        )
    if rejected_explanations:
        notes.append(
            f"Объяснений отклонено сторожем текста (упоминали путь вне репозитория): "
            f"{rejected_explanations} — сам вердикт и evidence это не затронуло"
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


def _candidate_paths(extraction: Extraction) -> list[dict]:
    by_path: dict[str, list[str]] = {}
    for fact in extraction.facts:
        if not fact.path:
            continue
        bases = by_path.setdefault(fact.path, [])
        basis = fact.basis or fact.key
        if basis and basis not in bases and len(bases) < MAX_BASES_PER_PATH:
            bases.append(basis)
    paths = sorted(by_path)[:MAX_CANDIDATES]
    return [{"path": p, "facts": by_path[p]} for p in paths]


def _validate_verdict(
    raw: object, known_ids: set[str], seen_ids: set[str], tree: Tree
) -> ItemVerdict | None:
    if not isinstance(raw, dict):
        return None
    item_id = str(raw.get("item_id") or "").strip()
    if item_id not in known_ids or item_id in seen_ids:
        return None
    status = str(raw.get("status") or "").strip().upper()
    if status not in STATUSES:
        return None
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

    return ItemVerdict(item_id=item_id, status=status, explanation=explanation, evidence=evidence)


def _guard_explanation(verdict: ItemVerdict, tree: Tree) -> bool:
    """Отклонить объяснение matcher'а, если оно упоминает путь вне репозитория.

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


def _parse(raw: str) -> list[dict] | None:
    match = _JSON_BLOCK.search(raw or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    verdicts = data.get("verdicts") if isinstance(data, dict) else None
    return verdicts if isinstance(verdicts, list) else None
