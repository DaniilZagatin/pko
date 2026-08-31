"""Реестр canonical stage продукта — независимый от отдельных snapshot'ов.

Источник истины для матчинга (`pko.versioning.canonical`): копит все
формулировки одного и того же бизнес-этапа, виденные когда-либо у продукта, а
не только в последнем snapshot — переименование «туда и обратно» через
несколько проверок матчится так же надёжно, как однократное.
"""

from __future__ import annotations

import json
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pko.store.db import connect


@dataclass(frozen=True)
class CanonicalStage:
    id: str
    product_id: str
    canonical_name: str
    aliases: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "product_id": self.product_id,
                "canonical_name": self.canonical_name, "aliases": self.aliases}


def list_stages(product_id: str, db_path: Path | None = None) -> list[CanonicalStage]:
    """От старых к новым — детерминированный порядок для матчинга (первый

    подходящий по порядку выигрывает при равенстве fuzzy-коэффициента).
    """
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT id, product_id, canonical_name, aliases_json FROM canonical_stages "
            "WHERE product_id = ? ORDER BY created_at ASC, rowid ASC",
            (product_id,),
        ).fetchall()
    return [
        CanonicalStage(
            id=row["id"], product_id=row["product_id"], canonical_name=row["canonical_name"],
            aliases=json.loads(row["aliases_json"]),
        )
        for row in rows
    ]


def create_stage(
    product_id: str, canonical_name: str, alias: str, db_path: Path | None = None
) -> CanonicalStage:
    aliases = [alias] if alias else []
    stage = CanonicalStage(
        id="cs_" + uuid.uuid4().hex[:12], product_id=product_id,
        canonical_name=canonical_name, aliases=aliases,
    )
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO canonical_stages (id, product_id, canonical_name, aliases_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (stage.id, product_id, canonical_name, json.dumps(aliases, ensure_ascii=False),
             time.strftime("%Y-%m-%dT%H:%M:%S")),
        )
        conn.commit()
    return stage


def add_alias(stage_id: str, alias: str, db_path: Path | None = None) -> None:
    """Добавить формулировку, если её ещё нет — идемпотентно: неизменившаяся

    формулировка не должна раздувать `aliases_json` дублями при каждой проверке.
    """
    if not alias:
        return
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT aliases_json FROM canonical_stages WHERE id = ?", (stage_id,)
        ).fetchone()
        if row is None:
            return
        aliases = json.loads(row["aliases_json"])
        if alias in aliases:
            return
        aliases.append(alias)
        conn.execute(
            "UPDATE canonical_stages SET aliases_json = ? WHERE id = ?",
            (json.dumps(aliases, ensure_ascii=False), stage_id),
        )
        conn.commit()
