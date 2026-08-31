"""Стабильный идентификатор этапа поперёк снимков одного продукта.

Проблема: `PlanItem.id`/`ItemVerdict.item_id` (`progress/schema.py`) —
обычный ключ одного прогона (вида `slide-N-k`), не переживает переформулировку
или переупорядочивание этапов на слайдах между проверками. Здесь — то, что в
плане версионирования названо `canonical_stage_id`: устойчивый идентификатор
одного и того же бизнес-этапа поперёк любого числа проверок продукта.

Матчинг — три уровня по возрастанию стоимости:
  1. точное совпадение нормализованного текста среди уже известных алиасов
     этапов продукта (`pko.store.canonical`);
  2. `difflib.SequenceMatcher` по тому же тексту — тоже код, не LLM;
  3. остаток — один batched LLM-вызов (роль `matcher`, уже обязательная для
     самого пайплайна анализа — отдельную роль в реестр не заводим).

Вызывается ПОСЛЕ того, как `run_progress()` уже вернул независимый
`ProgressModel`: агент, оценивающий текущую версию, никогда не видит
предыдущий снимок (план версионирования, §6) — иначе оценка готовности
смещалась бы в сторону ожидаемого прогресса.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from pko.errors import LlmError
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.schema import ItemVerdict, PlanItem, ProgressModel
from pko.store import canonical as canonical_store
from pko.store import snapshots as snapshots_store

FUZZY_THRESHOLD = 0.82
LLM_CONFIDENCE_THRESHOLD = 0.7

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

_LLM_SYSTEM = (
    "Ты сопоставляешь этапы плана между двумя проверками одного и того же продукта. "
    "Тебе даны формулировки этапов, оставшиеся без пары после точного и приблизительного "
    "текстового сравнения кодом: `old` — из предыдущей проверки, `new` — из текущей. Для "
    "каждой пары, которая по смыслу описывает один и тот же этап (даже если полностью "
    "переформулирована), верни объект {\"old_stage_id\":..., \"new_stage_id\":..., "
    "\"same_stage\": true, \"confidence\": число от 0 до 1}. Новому этапу нет пары среди "
    "старых — не включай его в ответ вообще. Один старый этап не может быть парой для "
    "двух разных новых. Ответ — JSON-массив таких объектов, без пояснений и без обёртки."
)


def normalize(text: str) -> str:
    text = _PUNCTUATION.sub(" ", text.strip().lower())
    return _WHITESPACE.sub(" ", text).strip()


def _item_text(item: PlanItem | None, fallback_id: str) -> str:
    if item is None:
        return fallback_id
    parts = [item.title, item.stage, item.description]
    return normalize(" ".join(p for p in parts if p))


def assign_canonical_ids(
    product_id: str,
    model: ProgressModel,
    spec: ModelSpec | None,
    client: ChatClient | None = None,
    db_path=None,
) -> None:
    """Проставить `verdict.canonical_stage_id` каждому вердикту `model`,

    используя накопленный реестр этапов продукта. Мутирует `model.verdicts`
    на месте. Первый snapshot продукта (реестр ещё пуст) — каждый item
    становится новым canonical stage без единого LLM-вызова: сравнивать не
    с чем. `db_path` — тестовая инъекция, тем же принципом, что и у
    `pko.store.*` (без неё — реальный `~/.pko/store.db`).
    """
    known = canonical_store.list_stages(product_id, db_path=db_path)
    available: dict[str, list[str]] = {stage.id: list(stage.aliases) for stage in known}
    canonical_names: dict[str, str] = {stage.id: stage.canonical_name for stage in known}
    existing_ids = set(available)

    texts: dict[str, str] = {}
    unresolved: list[ItemVerdict] = []
    for verdict in model.verdicts:
        item = model.items.get(verdict.item_id)
        text = _item_text(item, verdict.item_id)
        texts[verdict.item_id] = text

        matched = _match_exact(text, available) or _match_fuzzy(text, available)
        if matched:
            verdict.canonical_stage_id = matched
            available.pop(matched, None)
        else:
            unresolved.append(verdict)

    if unresolved and available:
        _match_via_llm(unresolved, texts, available, canonical_names, spec, client)

    for verdict in unresolved:
        if verdict.canonical_stage_id:
            continue
        item = model.items.get(verdict.item_id)
        title = item.title if item else verdict.item_id
        stage = canonical_store.create_stage(product_id, title, texts[verdict.item_id], db_path=db_path)
        verdict.canonical_stage_id = stage.id

    # Зафиксировать новую формулировку уже существующего этапа — следующая
    # проверка должна узнать его и в этом виде, даже если переформулирует ещё раз.
    for verdict in model.verdicts:
        if verdict.canonical_stage_id in existing_ids:
            canonical_store.add_alias(verdict.canonical_stage_id, texts[verdict.item_id], db_path=db_path)


def _match_exact(text: str, available: dict[str, list[str]]) -> str | None:
    for stage_id, aliases in available.items():
        if text in aliases:
            return stage_id
    return None


def _match_fuzzy(text: str, available: dict[str, list[str]]) -> str | None:
    best_id: str | None = None
    best_ratio = FUZZY_THRESHOLD
    for stage_id, aliases in available.items():
        for alias in aliases:
            ratio = SequenceMatcher(None, text, alias).ratio()
            if ratio >= best_ratio:
                best_id, best_ratio = stage_id, ratio
    return best_id


def _match_via_llm(
    unresolved: list[ItemVerdict],
    texts: dict[str, str],
    available: dict[str, list[str]],
    canonical_names: dict[str, str],
    spec: ModelSpec | None,
    client: ChatClient | None,
) -> None:
    """Остаток после точного и fuzzy-совпадения — один batched-запрос, не по

    одному на этап. Без настроенного `matcher` (не должно случаться в
    рабочем пайплайне — эта роль обязательна для самого анализа, но
    вызывающая сторона может передать `None` в тестах) — просто пропускаем,
    все `unresolved` станут новыми этапами дальше в `assign_canonical_ids`.
    """
    if spec is None:
        return
    old_stages = [
        {"old_stage_id": stage_id, "text": " | ".join([canonical_names.get(stage_id, ""), *aliases])}
        for stage_id, aliases in available.items()
    ]
    new_stages = [{"new_stage_id": v.item_id, "text": texts[v.item_id]} for v in unresolved]
    if not old_stages or not new_stages:
        return

    user = json.dumps({"old": old_stages, "new": new_stages}, ensure_ascii=False)
    chat_client = client if client is not None else ChatClient(spec=spec)
    try:
        raw = chat_client.complete(system=_LLM_SYSTEM, user=user, max_tokens=1000)
    except LlmError:
        return
    try:
        pairs = json.loads(raw)
    except json.JSONDecodeError:
        return
    if not isinstance(pairs, list):
        return

    by_new_id = {v.item_id: v for v in unresolved}
    claimed_old: set[str] = set()
    for pair in pairs:
        if not isinstance(pair, dict) or not pair.get("same_stage"):
            continue
        try:
            confidence = float(pair.get("confidence", 0))
        except (TypeError, ValueError):
            continue
        if confidence < LLM_CONFIDENCE_THRESHOLD:
            continue
        old_id = str(pair.get("old_stage_id") or "")
        new_id = str(pair.get("new_stage_id") or "")
        if old_id in claimed_old or old_id not in available or new_id not in by_new_id:
            continue
        verdict = by_new_id[new_id]
        if verdict.canonical_stage_id:
            continue
        verdict.canonical_stage_id = old_id
        claimed_old.add(old_id)


def save_snapshot_with_matching(
    product_id: str,
    model: ProgressModel,
    source: dict[str, Any],
    spec: ModelSpec | None,
    client: ChatClient | None = None,
    db_path=None,
) -> snapshots_store.Snapshot:
    """То, чем `web.analyses` пользуется вместо голого

    `store.snapshots.save_snapshot`, когда прогон привязан к продукту:
    сначала проставить `canonical_stage_id` (даже для первого snapshot
    продукта — тривиально, новые этапы), потом сохранить.
    """
    assign_canonical_ids(product_id, model, spec, client, db_path=db_path)
    return snapshots_store.save_snapshot(product_id, model, source, db_path=db_path)
