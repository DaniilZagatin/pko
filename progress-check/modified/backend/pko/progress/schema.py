"""Модель пункта плана, извлечённого из презентации.

Намеренно не расширяет `pko.model.schema.PkoModel`/`PkoObject` — у них закрытый
перечень `KINDS` (§4), которым пользуется остальной PKO (`checks.validator`,
таксономия), и он не про сравнение план/факт. Вместо этого пункт плана
переиспользует уже готовую форму `Evidence`/`Field` — «значение + происхождение
+ ссылка на факт» — только источником факта здесь может быть либо слайд
презентации, либо строка кода целевого репозитория.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Вердикт по пункту плана. Отдельный от `pko.model.schema.ORIGINS`: там —
# надёжность значения поля, здесь — состояние работы. `UNCLEAR` — честный
# результат, когда доказательств недостаточно ни на «сделано», ни на «не начато».
STATUSES = ("DONE", "PARTIAL", "NOT_STARTED", "UNCLEAR")


@dataclass(frozen=True)
class PlanItem:
    """Один пункт плана: задача или этап, взятый со слайда."""

    id: str
    title: str
    stage: str = ""
    description: str = ""
    source_slide: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "stage": self.stage,
            "description": self.description,
            "source_slide": self.source_slide,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PlanItem":
        return PlanItem(
            id=d["id"],
            title=d["title"],
            stage=d.get("stage", ""),
            description=d.get("description", ""),
            source_slide=int(d.get("source_slide") or 0),
        )


@dataclass(frozen=True)
class EvidenceRef:
    """Одна ссылка на код, подтверждающая (или нет) пункт плана.

    `verified=False` не отбрасывается — понижается и остаётся в отчёте с
    причиной, тем же принципом, что и `pko.model.schema.Field`: утверждение без
    доказательства не публикуется как «код показывает».
    """

    path: str
    line: int | None
    basis: str
    verified: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path, "line": self.line, "basis": self.basis,
            "verified": self.verified, "reason": self.reason,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "EvidenceRef":
        return EvidenceRef(
            path=d["path"], line=d.get("line"), basis=d.get("basis", ""),
            verified=bool(d.get("verified", False)), reason=d.get("reason", ""),
        )


@dataclass
class ItemVerdict:
    """Вердикт роли matcher по одному пункту плана."""

    item_id: str
    status: str
    explanation: str
    evidence: list[EvidenceRef] = field(default_factory=list)
    # Оценка агента, насколько пункт готов, 0-100 — для заливки шеврона в
    # дашборде-пути. В отличие от evidence, код не может это проверить, только
    # показать как есть, поэтому единственная гарантия здесь — диапазон.
    progress: int = 0
    # Устойчивый идентификатор этапа поперёк снимков одного продукта
    # (`pko.versioning.canonical`) — в отличие от `item_id` (обычный ключ
    # одного прогона, вида `slide-N-k`), переживает переформулировку и
    # переупорядочивание этапов между проверками. Пусто у разового анализа
    # без привязки к продукту — там сравнивать не с чем.
    canonical_stage_id: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"неизвестный статус пункта плана: {self.status}")
        self.progress = max(0, min(100, int(self.progress)))

    @property
    def verified_evidence(self) -> list[EvidenceRef]:
        return [e for e in self.evidence if e.verified]

    @property
    def is_grounded(self) -> bool:
        """Есть ли у вердикта хоть одна подтверждённая ссылка.

        `DONE`/`PARTIAL` без единой проверенной evidence — это ровно то, от
        чего Gate предостерегает («PASS без доказательства не является
        результатом», §5.2.3.2): здесь вердикт не отбрасывается, но отчёт
        обязан явно показать, что он не подтверждён кодом.
        """
        return bool(self.verified_evidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "status": self.status,
            "explanation": self.explanation,
            "grounded": self.is_grounded,
            "progress": self.progress,
            "evidence": [e.to_dict() for e in self.evidence],
            "canonical_stage_id": self.canonical_stage_id,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ItemVerdict":
        """Обратная сторона `to_dict()`. `grounded` не читается — это

        производное свойство (`is_grounded`), не хранимое поле; пересчитывается
        из `evidence` при каждом обращении.
        """
        return ItemVerdict(
            item_id=d["item_id"],
            status=d["status"],
            explanation=d.get("explanation", ""),
            evidence=[EvidenceRef.from_dict(e) for e in d.get("evidence", [])],
            progress=int(d.get("progress", 0)),
            canonical_stage_id=d.get("canonical_stage_id", ""),
        )


@dataclass(frozen=True)
class UnclaimedGroup:
    """Группа файлов кода, не подтвердивших ни одного пункта плана.

    Не вердикт «сделано сверх плана» — только сырой кандидат для него.
    Детерминированная разница множеств, без суждения модели: решить, действительно
    ли это дополнительная работа, а не инфраструктура или чужой периметр, может
    только человек, читающий отчёт.
    """

    group: str
    example_paths: list[str]
    file_count: int

    def to_dict(self) -> dict[str, Any]:
        return {"group": self.group, "example_paths": self.example_paths,
                "file_count": self.file_count}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "UnclaimedGroup":
        return UnclaimedGroup(
            group=d["group"], example_paths=list(d.get("example_paths", [])),
            file_count=int(d.get("file_count", 0)),
        )


@dataclass
class ProgressModel:
    """Итоговая модель одного прогона: план, вердикты, кандидаты сверх плана."""

    meta: dict[str, Any] = field(default_factory=dict)
    items: dict[str, PlanItem] = field(default_factory=dict)
    verdicts: list[ItemVerdict] = field(default_factory=list)
    unclaimed: list[UnclaimedGroup] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    # Связный вывод роли reporter по итогам всех пунктов сразу — необязателен,
    # пустая строка значит «не сформирован», не «пустой отчёт». `summary_source`
    # ("llm"/"none") — то же различие авторства, что и у остальных текстов PKO:
    # читатель должен видеть, что перед ним — написанное моделью или ничего.
    summary: str = ""
    summary_source: str = "none"

    def counts(self) -> dict[str, int]:
        out = {status: 0 for status in STATUSES}
        for v in self.verdicts:
            out[v.status] = out.get(v.status, 0) + 1
        return out

    def progress_ratio(self) -> float:
        """Доля DONE среди всех пунктов плана; PARTIAL не засчитывается наполовину.

        Округление в пользу пункта — искажение в другую сторону: отчёт о
        прогрессе не должен выглядеть более готовым, чем показывает код.
        """
        total = len(self.verdicts)
        if not total:
            return 0.0
        return self.counts().get("DONE", 0) / total

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "pko-progress-model/0.1",
            "meta": self.meta,
            "counts": self.counts(),
            "progress_ratio": round(self.progress_ratio(), 4),
            "items": {item_id: item.to_dict() for item_id, item in self.items.items()},
            "verdicts": [v.to_dict() for v in self.verdicts],
            "unclaimed": [u.to_dict() for u in self.unclaimed],
            "gaps": self.gaps,
            "summary": self.summary,
            "summary_source": self.summary_source,
        }

    def to_json(self) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "ProgressModel":
        """Обратная сторона `to_dict()` — нужна, чтобы снимок можно было

        сохранить и потом поднять обратно (`pko.store.snapshots`), не только
        напечатать. `counts()`/`progress_ratio()` не читаются из `d` — они
        производные и пересчитываются из `verdicts` при обращении.
        """
        return ProgressModel(
            meta=dict(d.get("meta", {})),
            items={k: PlanItem.from_dict(v) for k, v in d.get("items", {}).items()},
            verdicts=[ItemVerdict.from_dict(v) for v in d.get("verdicts", [])],
            unclaimed=[UnclaimedGroup.from_dict(u) for u in d.get("unclaimed", [])],
            gaps=list(d.get("gaps", [])),
            summary=d.get("summary", ""),
            summary_source=d.get("summary_source", "none"),
        )

    @staticmethod
    def from_json(text: str) -> "ProgressModel":
        import json

        return ProgressModel.from_dict(json.loads(text))
