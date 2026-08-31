"""Веб-логика продуктов и снимков — тонкий слой над `pko.store` для `web/app.py`,

тем же разделением, что и `web/analyses.py` (роут в `app.py`, логика здесь).
Сам прогон анализа по-прежнему делает `web.analyses` — этот модуль только
хранит его результат под продуктом и отдаёт историю наружу.
"""

from __future__ import annotations

from typing import Any

from pko.errors import PkoError
from pko.llm.registry import ModelSpec
from pko.store import comparisons as comparisons_store
from pko.store import products as products_store
from pko.store import snapshots as snapshots_store
from pko.versioning.diff import VersionComparison, compute_comparison
from pko.versioning.interpret import interpret_comparison
from pko.web.analyses import dashboard_json


def create_product(name: str) -> dict[str, Any]:
    return products_store.create_product(name).to_dict()


def list_products() -> list[dict[str, Any]]:
    return [summary.to_dict() for summary in products_store.list_products()]


def get_product(product_id: str) -> dict[str, Any]:
    return get_product_or_404(product_id).to_dict()


def get_product_or_404(product_id: str) -> products_store.Product:
    product = products_store.get_product(product_id)
    if product is None:
        raise PkoError("Продукт не найден.", hint=f"неизвестный product_id: {product_id!r}")
    return product


def list_snapshots(product_id: str) -> list[dict[str, Any]]:
    get_product_or_404(product_id)
    return [snapshot.summary_dict() for snapshot in snapshots_store.list_snapshots(product_id)]


def _get_snapshot_or_404(product_id: str, snapshot_id: str) -> snapshots_store.Snapshot:
    snapshot = snapshots_store.get_snapshot(snapshot_id)
    if snapshot is None or snapshot.product_id != product_id:
        raise PkoError("Проверка не найдена.", hint=f"неизвестный snapshot_id: {snapshot_id!r}")
    return snapshot


def get_snapshot_dashboard(product_id: str, snapshot_id: str) -> dict[str, Any]:
    """Снимок в том же JSON-контракте, что и готовый анализ (`_dashboard_json`)

    — фронтенду для «Текущего состояния» продукта не нужен второй формат.
    """
    get_product_or_404(product_id)
    snapshot = _get_snapshot_or_404(product_id, snapshot_id)
    result = dashboard_json(snapshot.model)
    result.update({
        "snapshot_id": snapshot.id,
        "version_number": snapshot.version_number,
        "created_at": snapshot.created_at,
        "source": snapshot.source,
    })
    return result


def compare_snapshots(
    product_id: str, from_snapshot_id: str, to_snapshot_id: str, reporter: ModelSpec | None = None,
) -> dict[str, Any]:
    """Сравнение двух snapshot'ов продукта: детерминированные факты

    (`versioning.diff`) плюс бизнес-интерпретация LLM поверх них
    (`versioning.interpret`) — принцип §10 плана версионирования: факт
    изменения решает код, смысл изменения объясняет LLM.

    Оба слоя кэшируются по тройке (product_id, from, to) отдельно — факты
    сразу (snapshots неизменяемы, пересчитывать нечего, §32 плана), а
    интерпретация только после успешного вызова reporter'а: если роль на
    момент первого запроса не была настроена, следующий запрос пробует
    снова, а не залипает в пустом результате навсегда.
    """
    get_product_or_404(product_id)
    from_snapshot = _get_snapshot_or_404(product_id, from_snapshot_id)
    to_snapshot = _get_snapshot_or_404(product_id, to_snapshot_id)
    if from_snapshot.version_number >= to_snapshot.version_number:
        raise PkoError(
            "Сравнивать можно только от более ранней проверки к более поздней.",
            hint=f"from: версия {from_snapshot.version_number}, "
                 f"to: версия {to_snapshot.version_number}",
        )

    cached = comparisons_store.get(product_id, from_snapshot_id, to_snapshot_id)
    if cached is None:
        comparison = compute_comparison(from_snapshot.model, to_snapshot.model)
        facts = comparison.to_dict()
        comparisons_store.save_facts(product_id, from_snapshot_id, to_snapshot_id, facts)
    else:
        facts = cached.facts
        comparison = VersionComparison.from_dict(facts)

    interpretation = cached.interpretation if cached is not None else None
    if interpretation is None:
        interpreted = interpret_comparison(comparison, from_snapshot.model, to_snapshot.model, reporter)
        interpretation = interpreted.to_dict()
        if interpreted.source == "llm":
            comparisons_store.save_interpretation(product_id, from_snapshot_id, to_snapshot_id, interpretation)

    return _merge(facts, interpretation)


def _merge(facts: dict[str, Any], interpretation: dict[str, Any]) -> dict[str, Any]:
    """Наложить бизнес-интерпретацию на факты: `business_delta` — в каждый

    элемент `stage_deltas` по `canonical_stage_id`, остальное — поверх
    пустых значений по умолчанию из `facts`.
    """
    merged = {**facts, "stage_deltas": [dict(delta) for delta in facts["stage_deltas"]]}
    business_deltas = interpretation.get("stage_business_deltas", {})
    for stage in merged["stage_deltas"]:
        business = business_deltas.get(stage["canonical_stage_id"])
        if business:
            stage["business_delta"] = business
    merged["progress_summary"] = interpretation.get("progress_summary") or merged.get("progress_summary", "")
    merged["current_risks"] = interpretation.get("current_risks") or merged.get("current_risks", [])
    merged["next_focus"] = interpretation.get("next_focus") or merged.get("next_focus", [])
    return merged
