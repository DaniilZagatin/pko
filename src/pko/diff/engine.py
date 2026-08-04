"""Структурное сравнение двух версий модели.

Счётчики и статусы «добавлено / удалено / изменено» считает код, а не языковая
модель: именно из-за этого в отчёте нельзя получить фразу «BBB стало восемь»,
которой не соответствует ни один объект.

Объекты сопоставляются сначала по идентификатору, затем по сигнатуре (тип,
название, пакет, пересечение кандидатов) — иначе переименование или сдвиг
нумерации выглядели бы как удаление и создание.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pko.model.schema import KINDS, PkoModel, PkoObject

ADDED = "ADDED"
REMOVED = "REMOVED"
CHANGED = "CHANGED"
SAME = "SAME"


@dataclass
class FieldChange:
    label: str
    before: str
    after: str


@dataclass
class ObjectDiff:
    status: str
    kind: str
    id: str
    name: str
    changes: list[FieldChange] = field(default_factory=list)


@dataclass
class ModelDiff:
    left_label: str
    right_label: str
    left_meta: dict = field(default_factory=dict)
    right_meta: dict = field(default_factory=dict)
    counts_left: dict[str, int] = field(default_factory=dict)
    counts_right: dict[str, int] = field(default_factory=dict)
    objects: list[ObjectDiff] = field(default_factory=list)

    def by_kind(self, kind: str) -> list[ObjectDiff]:
        return [o for o in self.objects if o.kind == kind]

    def summary(self) -> dict[str, int]:
        return {
            ADDED: sum(1 for o in self.objects if o.status == ADDED),
            REMOVED: sum(1 for o in self.objects if o.status == REMOVED),
            CHANGED: sum(1 for o in self.objects if o.status == CHANGED),
            SAME: sum(1 for o in self.objects if o.status == SAME),
        }


def diff_models(left: PkoModel, right: PkoModel) -> ModelDiff:
    """Сравнить старую версию (left) с новой (right)."""
    result = ModelDiff(
        left_label=str(left.meta.get("version_label", "v1")),
        right_label=str(right.meta.get("version_label", "current")),
        left_meta=dict(left.meta),
        right_meta=dict(right.meta),
        counts_left=left.counts(),
        counts_right=right.counts(),
    )

    unmatched_right = {o.id: o for o in right.objects}
    matched: dict[str, PkoObject] = {}

    # Проход 1. Объекты с устойчивым ключом (guardrails) сопоставляются по нему.
    # Номер GRD-NNN позиционный: добавление нового лимита сдвигает нумерацию всех
    # последующих политик, и сопоставление по идентификатору выдавало бы чужую пару —
    # неизменившееся ограничение выглядело бы изменённым, а соседнее — новым.
    for lo in left.objects:
        key = _stable_key(lo)
        if key is None:
            continue
        for ro in list(unmatched_right.values()):
            if _stable_key(ro) == key:
                matched[lo.id] = unmatched_right.pop(ro.id)
                break

    # Объекты с ключом дальше не сопоставляются: не нашлась пара по ключу — значит,
    # политики с таким ключом в новой версии нет. Совпадение номера здесь ничего не
    # доказывает и вернуло бы ту же подмену пар.
    keyed_left = {lo.id for lo in left.objects if _stable_key(lo) is not None}
    free_right = {rid: ro for rid, ro in unmatched_right.items() if _stable_key(ro) is None}

    # Проход 2. Остальные — по идентификатору.
    for lo in left.objects:
        if lo.id in matched or lo.id in keyed_left:
            continue
        ro = free_right.pop(lo.id, None)
        if ro is not None:
            matched[lo.id] = unmatched_right.pop(ro.id)

    # Проход 3. Что осталось — по сигнатуре: имя, пакет, пересечение кандидатов.
    for lo in left.objects:
        if lo.id in matched or lo.id in keyed_left:
            continue
        ro = _match_by_signature(lo, free_right)
        if ro is not None:
            free_right.pop(ro.id, None)
            matched[lo.id] = unmatched_right.pop(ro.id)

    pairs: list[tuple[PkoObject | None, PkoObject | None]] = [
        (lo, matched.get(lo.id)) for lo in left.objects
    ]
    pairs.extend((None, ro) for ro in unmatched_right.values())

    for lo, ro in pairs:
        result.objects.append(_compare(lo, ro))

    result.objects.sort(key=lambda o: (KINDS.index(o.kind), o.id))
    return result


def _stable_key(obj: PkoObject) -> tuple[str, str] | None:
    """Идентичность, не зависящая от позиции в списке.

    Номера BBB-NNN, AO-NNN и GRD-NNN присваиваются по порядку в отсортированном
    списке, поэтому один новый пакет, эффект или лимит сдвигает нумерацию всех
    последующих объектов. Сопоставление по номеру в такой ситуации соединяет
    разные объекты и выдаёт заведомо ложный список изменений.
    """
    key = obj.links.get("stable_key") or obj.links.get("limit_key")
    return (obj.kind, key[0]) if key else None


def _match_by_signature(obj: PkoObject, pool: dict[str, PkoObject]) -> PkoObject | None:
    best: PkoObject | None = None
    best_score = 0.0
    for candidate in pool.values():
        if candidate.kind != obj.kind:
            continue
        score = _similarity(obj, candidate)
        if score > best_score:
            best, best_score = candidate, score
    # Порог намеренно высокий: лучше показать «удалено + добавлено», чем
    # склеить два разных блока и спрятать изменение.
    return best if best_score >= 0.6 else None


def _similarity(a: PkoObject, b: PkoObject) -> float:
    score = 0.0
    if a.name == b.name:
        score += 0.5
    pkg_a = set(a.links.get("package", []))
    pkg_b = set(b.links.get("package", []))
    if pkg_a and pkg_a == pkg_b:
        score += 0.3
    if a.candidates and b.candidates:
        overlap = len(set(a.candidates) & set(b.candidates))
        union = len(set(a.candidates) | set(b.candidates))
        score += 0.4 * (overlap / union if union else 0)
    return score


def _compare(left: PkoObject | None, right: PkoObject | None) -> ObjectDiff:
    if left is None and right is not None:
        return ObjectDiff(status=ADDED, kind=right.kind, id=right.id, name=right.name)
    if right is None and left is not None:
        return ObjectDiff(status=REMOVED, kind=left.kind, id=left.id, name=left.name)

    assert left is not None and right is not None
    changes: list[FieldChange] = []
    labels = list(dict.fromkeys(list(left.fields) + list(right.fields)))
    for label in labels:
        before = left.fields[label].text() if label in left.fields else "—"
        after = right.fields[label].text() if label in right.fields else "—"
        if before != after:
            changes.append(FieldChange(label=label, before=before, after=after))

    status = CHANGED if changes or left.name != right.name else SAME
    return ObjectDiff(status=status, kind=right.kind, id=right.id, name=right.name, changes=changes)
