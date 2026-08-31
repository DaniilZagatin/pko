"""Детерминированное сравнение двух снимков продукта.

Принцип плана версионирования (§10): факт изменения вычисляет обычный код,
смысл изменения — LLM (`versioning/interpret.py`). Здесь — только факт: не
дергает сеть, не знает про LLM, не умеет `SCOPE_CHANGED` (это единственный
change_type, для которого нужно семантическое суждение, а не арифметика по
статусам — план версионирования сам относит его к «после MVP», §39).

Обе модели обязаны иметь проставленный `canonical_stage_id`
(`versioning.canonical.assign_canonical_ids`) — без него вердикт молча
выпадает из сравнения (см. `compute_comparison`), это ответственность
вызывающей стороны (снимок без матчинга сюда попасть не должен).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pko.progress.schema import ItemVerdict, ProgressModel
from pko.render.progress_report import display_percent

# Порядок статусов для сравнения. UNCLEAR — между NOT_STARTED и PARTIAL: это
# честное «доказательств не хватило», ближе к отсутствию прогресса, чем к
# частичной готовности (та же семантика, что и в progress/schema.py::STATUSES).
STATUS_ORDER = {"NOT_STARTED": 0, "UNCLEAR": 1, "PARTIAL": 2, "DONE": 3}

IMPROVED = "IMPROVED"
UNCHANGED = "UNCHANGED"
REGRESSED = "REGRESSED"
ADDED = "ADDED"
REMOVED = "REMOVED"


@dataclass(frozen=True)
class StageDelta:
    canonical_stage_id: str
    title: str
    previous_status: str | None
    current_status: str | None
    previous_readiness: int | None
    current_readiness: int | None
    readiness_delta: int | None
    change_type: str
    # Заполняется отдельно, LLM-интерпретацией (versioning/interpret.py, фаза
    # 3 плана) — здесь всегда "", это не то, что вычисляет эта функция.
    business_delta: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_stage_id": self.canonical_stage_id,
            "title": self.title,
            "previous_status": self.previous_status,
            "current_status": self.current_status,
            "previous_readiness": self.previous_readiness,
            "current_readiness": self.current_readiness,
            "readiness_delta": self.readiness_delta,
            "change_type": self.change_type,
            "business_delta": self.business_delta,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "StageDelta":
        return StageDelta(
            canonical_stage_id=d["canonical_stage_id"],
            title=d.get("title", ""),
            previous_status=d.get("previous_status"),
            current_status=d.get("current_status"),
            previous_readiness=d.get("previous_readiness"),
            current_readiness=d.get("current_readiness"),
            readiness_delta=d.get("readiness_delta"),
            change_type=d["change_type"],
            business_delta=d.get("business_delta", ""),
        )


@dataclass
class VersionComparison:
    readiness_before: float
    readiness_after: float
    readiness_delta: float
    stage_deltas: list[StageDelta] = field(default_factory=list)
    # Тоже LLM-слой (фаза 3) — здесь пустые значения по умолчанию.
    progress_summary: str = ""
    current_risks: list[dict[str, str]] = field(default_factory=list)
    next_focus: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "readiness_before": self.readiness_before,
            "readiness_after": self.readiness_after,
            "readiness_delta": round(self.readiness_delta, 4),
            "stage_deltas": [d.to_dict() for d in self.stage_deltas],
            "progress_summary": self.progress_summary,
            "current_risks": self.current_risks,
            "next_focus": self.next_focus,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "VersionComparison":
        """Обратная сторона `to_dict()` — нужна, чтобы поднять закэшированные

        факты сравнения (`pko.store.comparisons`) обратно в объект, когда
        интерпретация ещё не посчитана и её нужно досчитать по уже готовым
        фактам, не пересчитывая их.
        """
        return VersionComparison(
            readiness_before=d["readiness_before"],
            readiness_after=d["readiness_after"],
            readiness_delta=d["readiness_delta"],
            stage_deltas=[StageDelta.from_dict(sd) for sd in d.get("stage_deltas", [])],
            progress_summary=d.get("progress_summary", ""),
            current_risks=list(d.get("current_risks", [])),
            next_focus=list(d.get("next_focus", [])),
        )


def compute_comparison(from_model: ProgressModel, to_model: ProgressModel) -> VersionComparison:
    by_stage_from = {v.canonical_stage_id: v for v in from_model.verdicts if v.canonical_stage_id}
    by_stage_to = {v.canonical_stage_id: v for v in to_model.verdicts if v.canonical_stage_id}
    # Порядок: сначала этапы в порядке from (история), затем новые из to,
    # которых в from не было (ADDED) — тот же принцип, что и порядок пунктов
    # плана в остальном PKO: порядок появления, не алфавит и не id.
    all_ids = list(dict.fromkeys([*by_stage_from, *by_stage_to]))

    deltas = [
        _stage_delta(stage_id, by_stage_from.get(stage_id), by_stage_to.get(stage_id),
                     from_model, to_model)
        for stage_id in all_ids
    ]

    readiness_before = round(from_model.progress_ratio(), 4)
    readiness_after = round(to_model.progress_ratio(), 4)
    return VersionComparison(
        readiness_before=readiness_before,
        readiness_after=readiness_after,
        readiness_delta=readiness_after - readiness_before,
        stage_deltas=deltas,
    )


def _title_of(model: ProgressModel, verdict: ItemVerdict) -> str:
    item = model.items.get(verdict.item_id)
    return item.title if item else verdict.item_id


def _stage_delta(
    stage_id: str,
    prev: ItemVerdict | None,
    curr: ItemVerdict | None,
    from_model: ProgressModel,
    to_model: ProgressModel,
) -> StageDelta:
    if prev is None:
        assert curr is not None  # stage_id взят из объединения обоих множеств
        return StageDelta(
            canonical_stage_id=stage_id, title=_title_of(to_model, curr),
            previous_status=None, current_status=curr.status,
            previous_readiness=None, current_readiness=display_percent(curr),
            readiness_delta=None, change_type=ADDED,
        )
    if curr is None:
        return StageDelta(
            canonical_stage_id=stage_id, title=_title_of(from_model, prev),
            previous_status=prev.status, current_status=None,
            previous_readiness=display_percent(prev), current_readiness=None,
            readiness_delta=None, change_type=REMOVED,
        )

    previous_readiness = display_percent(prev)
    current_readiness = display_percent(curr)
    previous_rank = STATUS_ORDER.get(prev.status, 0)
    current_rank = STATUS_ORDER.get(curr.status, 0)
    if current_rank > previous_rank:
        change_type = IMPROVED
    elif current_rank < previous_rank:
        change_type = REGRESSED
    else:
        # Статус не изменился — но readiness внутри статуса мог (PARTIAL
        # 35%→70%): change_type остаётся UNCHANGED (план версионирования,
        # §9), а числовая динамика всё равно попадает в readiness_delta ниже.
        change_type = UNCHANGED

    return StageDelta(
        canonical_stage_id=stage_id, title=_title_of(to_model, curr),
        previous_status=prev.status, current_status=curr.status,
        previous_readiness=previous_readiness, current_readiness=current_readiness,
        readiness_delta=current_readiness - previous_readiness,
        change_type=change_type,
    )
