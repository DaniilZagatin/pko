"""Кэш сравнения двух snapshot'ов — детерминированные факты сразу, бизнес-

интерпретация LLM (`pko.versioning.interpret`) — когда посчитана. Ключ —
тройка (product_id, from, to): snapshots неизменяемы, поэтому факты не
пересчитываются повторно (план версионирования, §32).
"""

from __future__ import annotations

import json
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pko.store.db import connect


@dataclass(frozen=True)
class CachedComparison:
    facts: dict[str, Any]
    interpretation: dict[str, Any] | None


def get(
    product_id: str, from_id: str, to_id: str, db_path: Path | None = None
) -> CachedComparison | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT facts_json, interpretation_json FROM comparisons "
            "WHERE product_id = ? AND from_snapshot_id = ? AND to_snapshot_id = ?",
            (product_id, from_id, to_id),
        ).fetchone()
    if row is None:
        return None
    interpretation = json.loads(row["interpretation_json"]) if row["interpretation_json"] else None
    return CachedComparison(facts=json.loads(row["facts_json"]), interpretation=interpretation)


def save_facts(
    product_id: str, from_id: str, to_id: str, facts: dict[str, Any], db_path: Path | None = None
) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO comparisons "
            "(product_id, from_snapshot_id, to_snapshot_id, facts_json, interpretation_json) "
            "VALUES (?, ?, ?, ?, NULL) "
            "ON CONFLICT(product_id, from_snapshot_id, to_snapshot_id) "
            "DO UPDATE SET facts_json = excluded.facts_json",
            (product_id, from_id, to_id, json.dumps(facts, ensure_ascii=False)),
        )
        conn.commit()


def save_interpretation(
    product_id: str, from_id: str, to_id: str, interpretation: dict[str, Any],
    db_path: Path | None = None,
) -> None:
    with closing(connect(db_path)) as conn:
        conn.execute(
            "UPDATE comparisons SET interpretation_json = ? "
            "WHERE product_id = ? AND from_snapshot_id = ? AND to_snapshot_id = ?",
            (json.dumps(interpretation, ensure_ascii=False), product_id, from_id, to_id),
        )
        conn.commit()
