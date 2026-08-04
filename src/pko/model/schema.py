"""PKO-модель: объекты управления стандарта §4.1–4.6 с доказательствами на уровне поля.

Ключевое правило: значение поля не существует отдельно от происхождения и ссылки
на факт. Поле без доказательства не может иметь origin выше `INFERRED`
(это проверяет `pko.checks.validator`), а в отчёт попадают только путь, строка и
краткое основание — фрагменты кода не сохраняются и не публикуются.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

# Происхождение значения поля. Порядок = убывание надёжности.
ORIGINS = (
    "VERIFIED",         # подтверждено тестом или несколькими независимыми фактами
    "OBSERVED",         # найдено в коде
    "DECLARED",         # заявлено в business_intent / конфигурации / документации
    "INFERRED",         # логический вывод, факта нет
    "MANUAL_OVERRIDE",  # ручная правка владельца
    "UNKNOWN",          # данных недостаточно
)

# Объекты управления: потребность → путь → процесс → BBB → атомарная операция,
# guardrail — сквозной объект (§4).
KINDS = ("NEED", "JOURNEY", "PROCESS", "BBB", "AO", "GUARDRAIL")

_KIND_TITLES = {
    "NEED": "Потребность клиента",
    "JOURNEY": "Клиентский путь",
    "PROCESS": "Автономный процесс",
    "BBB": "BBB — Business Building Block",
    "AO": "Атомарная операция",
    "GUARDRAIL": "Guardrail / ограничение исполнения",
}


@dataclass(frozen=True)
class Evidence:
    """Прямая ссылка на факт: коммит, путь, строка и краткое основание.

    Соответствует облегчённому режиму доказательств BASIC (§5.2.4): источник
    однозначно идентифицируется, привязан к неизменяемой версии и к конкретному
    утверждению. Текст кода намеренно не хранится.
    """

    commit: str
    path: str
    line: int | None = None
    basis: str = ""

    def ref(self) -> str:
        loc = f"{self.path}:{self.line}" if self.line else self.path
        return f"{loc}@{self.commit[:8]}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Evidence":
        return Evidence(
            commit=d["commit"], path=d["path"], line=d.get("line"), basis=d.get("basis", "")
        )


@dataclass
class Field:
    """Значение поля паспорта вместе с происхождением и доказательствами."""

    value: Any
    origin: str = "UNKNOWN"
    evidence: list[Evidence] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.origin not in ORIGINS:
            raise ValueError(f"неизвестный origin: {self.origin}")

    @property
    def is_empty(self) -> bool:
        if self.value is None:
            return True
        if isinstance(self.value, (list, tuple, dict, str)):
            return len(self.value) == 0
        return False

    def text(self) -> str:
        if isinstance(self.value, (list, tuple)):
            return " · ".join(str(v) for v in self.value)
        if self.value is None:
            return "не установлено"
        return str(self.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "origin": self.origin,
            "evidence": [e.to_dict() for e in self.evidence],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Field":
        return Field(
            value=d.get("value"),
            origin=d.get("origin", "UNKNOWN"),
            evidence=[Evidence.from_dict(e) for e in d.get("evidence", [])],
        )


@dataclass
class PkoObject:
    """Один объект управления. `fields` — упорядоченные строки паспорта."""

    id: str
    kind: str
    name: str
    fields: dict[str, Field] = field(default_factory=dict)
    links: dict[str, list[str]] = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"неизвестный kind: {self.kind}")

    @property
    def kind_title(self) -> str:
        return _KIND_TITLES[self.kind]

    def set(self, label: str, value: Any, origin: str = "UNKNOWN",
            evidence: Iterable[Evidence] = ()) -> None:
        self.fields[label] = Field(value=value, origin=origin, evidence=list(evidence))

    def get_text(self, label: str, default: str = "") -> str:
        f = self.fields.get(label)
        return f.text() if f else default

    def all_evidence(self) -> list[Evidence]:
        out: list[Evidence] = []
        for f in self.fields.values():
            out.extend(f.evidence)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "links": self.links,
            "candidates": self.candidates,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PkoObject":
        return PkoObject(
            id=d["id"],
            kind=d["kind"],
            name=d["name"],
            fields={k: Field.from_dict(v) for k, v in d.get("fields", {}).items()},
            links={k: list(v) for k, v in d.get("links", {}).items()},
            candidates=list(d.get("candidates", [])),
        )


@dataclass
class Coverage:
    """Насколько репозиторий вообще был проанализирован.

    Без этой цифры пустая модель выглядит «чистой»: непокрытый фронтенд
    неотличим от отсутствующей функциональности.
    """

    files_total: int = 0
    files_analyzed: int = 0
    analyzed_globs: list[str] = field(default_factory=list)
    skipped_globs: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return (self.files_analyzed / self.files_total) if self.files_total else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ratio"] = round(self.ratio, 4)
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Coverage":
        return Coverage(
            files_total=d.get("files_total", 0),
            files_analyzed=d.get("files_analyzed", 0),
            analyzed_globs=list(d.get("analyzed_globs", [])),
            skipped_globs=list(d.get("skipped_globs", [])),
        )


@dataclass
class PkoModel:
    """Полная модель одной версии репозитория."""

    meta: dict[str, Any] = field(default_factory=dict)
    objects: list[PkoObject] = field(default_factory=list)
    coverage: Coverage = field(default_factory=Coverage)
    gaps: list[str] = field(default_factory=list)
    facts_count: int = 0

    # --- доступ -----------------------------------------------------------
    def by_kind(self, kind: str) -> list[PkoObject]:
        return [o for o in self.objects if o.kind == kind]

    def by_id(self, obj_id: str) -> PkoObject | None:
        for o in self.objects:
            if o.id == obj_id:
                return o
        return None

    def ids(self) -> set[str]:
        return {o.id for o in self.objects}

    def add(self, obj: PkoObject) -> PkoObject:
        if self.by_id(obj.id):
            raise ValueError(f"дубликат идентификатора объекта: {obj.id}")
        self.objects.append(obj)
        return obj

    def counts(self) -> dict[str, int]:
        return {k: len(self.by_kind(k)) for k in KINDS}

    def unknown_ratio(self) -> float:
        total = sum(len(o.fields) for o in self.objects)
        if not total:
            return 1.0
        unknown = sum(
            1 for o in self.objects for f in o.fields.values() if f.origin == "UNKNOWN"
        )
        return unknown / total

    # --- сериализация -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "pko-model/0.1",
            "meta": self.meta,
            "counts": self.counts(),
            "coverage": self.coverage.to_dict(),
            "facts_count": self.facts_count,
            "unknown_ratio": round(self.unknown_ratio(), 4),
            "gaps": self.gaps,
            "objects": [o.to_dict() for o in self.objects],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PkoModel":
        return PkoModel(
            meta=d.get("meta", {}),
            objects=[PkoObject.from_dict(o) for o in d.get("objects", [])],
            coverage=Coverage.from_dict(d.get("coverage", {})),
            gaps=list(d.get("gaps", [])),
            facts_count=d.get("facts_count", 0),
        )

    @staticmethod
    def from_json(text: str) -> "PkoModel":
        return PkoModel.from_dict(json.loads(text))
