"""Машинный срез наблюдений: `semantic_facts.json`.

Паспорта и таксономия отвечают человеку. Этот файл отвечает машине: в нём
лежат все наблюдения всех разобранных версий с фасетами, доказательствами и
статусом проверки — тем, чего в HTML нет и по чему нельзя ни сверить прогоны,
ни собрать метрику полноты.

Отдельный артефакт нужен ещё и потому, что универсализация вводит различие,
невидимое в паспорте: наблюдение может быть принято в отчёт, но исключено из
вердикта, если его механизм структурно не проверяется. Здесь это различие
названо явно, поэтому аудит не обязан догадываться о нём по тексту.

Файл записывается тем же транзакционным комплектом, что и отчёты: иначе
машинные данные и отчёт могли бы относиться к разным прогонам.
"""

from __future__ import annotations

import json
from typing import Any

from pko.extractors.base import Fact
from pko.model import taxonomy

SCHEMA = "pko-semantic-facts/0.1"


def _fact_to_dict(fact: Fact) -> dict[str, Any]:
    facets = fact.facets
    return {
        "kind": fact.kind,
        "category": facets.category,
        "action": facets.action,
        "mechanism": facets.mechanism,
        "key": fact.key,
        "path": fact.path,
        "line": fact.line,
        "basis": fact.basis,
        "gate_eligible": fact.gate_eligible,
        # Механизм без структурной проверки виден отдельно от «просто
        # неизвестного»: первое — граница инструмента, второе — плохая находка.
        "mechanism_known": taxonomy.is_known_mechanism(facets.mechanism),
    }


def build(versions: list[dict[str, Any]]) -> dict[str, Any]:
    """Собрать документ по разобранным версиям.

    `versions` — список словарей с ключами `label`, `commit`, `facts`,
    `gaps`, `packs`, `stack`.
    """
    out_versions = []
    for item in versions:
        facts = [_fact_to_dict(f) for f in item.get("facts", [])]
        by_category: dict[str, int] = {}
        by_mechanism: dict[str, int] = {}
        for row in facts:
            by_category[row["category"]] = by_category.get(row["category"], 0) + 1
            key = row["mechanism"] or "—"
            by_mechanism[key] = by_mechanism.get(key, 0) + 1
        out_versions.append({
            "label": item.get("label", ""),
            "commit": item.get("commit", ""),
            "packs": item.get("packs", []),
            "stack": item.get("stack", {}),
            "counts": {
                "facts": len(facts),
                "gate_eligible": sum(1 for r in facts if r["gate_eligible"]),
                "by_category": dict(sorted(by_category.items())),
                "by_mechanism": dict(sorted(by_mechanism.items())),
            },
            "gaps": list(item.get("gaps", [])),
            "facts": facts,
        })
    return {"schema": SCHEMA, "versions": out_versions}


def to_json(versions: list[dict[str, Any]]) -> str:
    return json.dumps(build(versions), ensure_ascii=False, indent=2)
