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


@dataclass
class ItemVerdict:
    """Вердикт роли matcher по одному пункту плана."""

    item_id: str
    status: str
    explanation: str
    evidence: list[EvidenceRef] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"неизвестный статус пункта плана: {self.status}")

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
            "evidence": [e.to_dict() for e in self.evidence],
        }


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
