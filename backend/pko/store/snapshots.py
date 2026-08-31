"""Снимок одной проверки продукта — `AnalysisSnapshot` из плана версионирования.

`ProgressModel` (результат одного независимого прогона пайплайна,
`progress/pipeline.py::run_progress`) сохраняется как есть, вместе с
метаданными источника материалов (`source`) и порядковым номером версии
внутри продукта. Snapshot неизменяем: новых прогонов в него не дописывают,
каждая проверка — новая строка с новым `version_number`.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pko.progress.schema import ProgressModel
from pko.store.db import connect

# Лок на продукт, не глобальный: два разных продукта сохраняются параллельно
# без ожидания друг друга, а два прогона ОДНОГО продукта, финишировавшие
# одновременно (два фоновых потока `web.analyses._execute`), не должны
# прочитать один и тот же MAX(version_number) и обе присвоить снимку одну и
# ту же следующую версию.
_locks_guard = threading.Lock()
_locks: dict[str, threading.Lock] = {}


def _lock_for(product_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(product_id, threading.Lock())


@dataclass(frozen=True)
class Snapshot:
    id: str
    product_id: str
    version_number: int
    created_at: str
    overall_readiness: float
    source: dict[str, Any]
    model: ProgressModel

    def summary_dict(self) -> dict[str, Any]:
        """То, что показывает список проверок продукта — без полной модели."""
        return {
            "id": self.id,
            "product_id": self.product_id,
            "version_number": self.version_number,
            "created_at": self.created_at,
            "overall_readiness": self.overall_readiness,
            "source": self.source,
        }


def save_snapshot(
    product_id: str,
    model: ProgressModel,
    source: dict[str, Any],
    db_path: Path | None = None,
) -> Snapshot:
    with _lock_for(product_id):
        with closing(connect(db_path)) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version_number), 0) FROM snapshots WHERE product_id = ?",
                (product_id,),
            ).fetchone()
            snapshot = Snapshot(
                id="snap_" + uuid.uuid4().hex[:12],
                product_id=product_id,
                version_number=int(row[0]) + 1,
                created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                overall_readiness=round(model.progress_ratio(), 4),
                source=source,
                model=model,
            )
            conn.execute(
                "INSERT INTO snapshots "
                "(id, product_id, version_number, created_at, overall_readiness, "
                " source_json, model_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot.id, snapshot.product_id, snapshot.version_number,
                    snapshot.created_at, snapshot.overall_readiness,
                    json.dumps(source, ensure_ascii=False), model.to_json(),
                ),
            )
            conn.commit()
    return snapshot


def list_snapshots(product_id: str, db_path: Path | None = None) -> list[Snapshot]:
    """От старых к новым — так, как рисуется временная шкала."""
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE product_id = ? ORDER BY version_number ASC",
            (product_id,),
        ).fetchall()
    return [_row_to_snapshot(row) for row in rows]


def get_latest_snapshot(product_id: str, db_path: Path | None = None) -> Snapshot | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE product_id = ? "
            "ORDER BY version_number DESC LIMIT 1",
            (product_id,),
        ).fetchone()
    return _row_to_snapshot(row) if row is not None else None


def get_snapshot(snapshot_id: str, db_path: Path | None = None) -> Snapshot | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
    return _row_to_snapshot(row) if row is not None else None


def _row_to_snapshot(row: Any) -> Snapshot:
    return Snapshot(
        id=row["id"],
        product_id=row["product_id"],
        version_number=row["version_number"],
        created_at=row["created_at"],
        overall_readiness=row["overall_readiness"],
        source=json.loads(row["source_json"]),
        model=ProgressModel.from_dict(json.loads(row["model_json"])),
    )
