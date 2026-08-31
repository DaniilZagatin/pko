"""Продукт — сущность, вокруг которой строится история проверок (Progress Mode).

Продукт создаётся и выбирается пользователем явно (см. форму загрузки) — не
матчится автоматически по URL репозитория: репозиторий переименовывают, а у
чисто файлового источника его вообще нет, см. план версии/сравнения.
"""

from __future__ import annotations

import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pko.errors import PkoError
from pko.store.db import connect


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "created_at": self.created_at}


@dataclass(frozen=True)
class ProductSummary:
    """Продукт вместе со сводкой по его последнему снимку — для списка продуктов."""

    product: Product
    snapshot_count: int
    latest_readiness: float | None
    latest_created_at: str | None

    def to_dict(self) -> dict[str, Any]:
        d = self.product.to_dict()
        d.update({
            "snapshot_count": self.snapshot_count,
            "latest_readiness": self.latest_readiness,
            "latest_created_at": self.latest_created_at,
        })
        return d


def create_product(name: str, db_path: Path | None = None) -> Product:
    name = name.strip()
    if not name:
        raise PkoError("Название продукта не может быть пустым.")
    product = Product(
        id="prod_" + uuid.uuid4().hex[:12],
        name=name,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    with closing(connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO products (id, name, created_at) VALUES (?, ?, ?)",
            (product.id, product.name, product.created_at),
        )
        conn.commit()
    return product


def get_product(product_id: str, db_path: Path | None = None) -> Product | None:
    with closing(connect(db_path)) as conn:
        row = conn.execute(
            "SELECT id, name, created_at FROM products WHERE id = ?", (product_id,)
        ).fetchone()
    if row is None:
        return None
    return Product(id=row["id"], name=row["name"], created_at=row["created_at"])


def list_products(db_path: Path | None = None) -> list[ProductSummary]:
    """Новые продукты сверху — так их удобнее находить сразу после создания."""
    with closing(connect(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.created_at,
                   COUNT(s.id) AS snapshot_count,
                   MAX(s.version_number) AS latest_version
            FROM products p
            LEFT JOIN snapshots s ON s.product_id = p.id
            GROUP BY p.id
            ORDER BY p.created_at DESC, p.rowid DESC
            """
        ).fetchall()
        summaries: list[ProductSummary] = []
        for row in rows:
            latest_readiness = None
            latest_created_at = None
            if row["latest_version"] is not None:
                latest = conn.execute(
                    "SELECT overall_readiness, created_at FROM snapshots "
                    "WHERE product_id = ? AND version_number = ?",
                    (row["id"], row["latest_version"]),
                ).fetchone()
                if latest is not None:
                    latest_readiness = latest["overall_readiness"]
                    latest_created_at = latest["created_at"]
            summaries.append(ProductSummary(
                product=Product(id=row["id"], name=row["name"], created_at=row["created_at"]),
                snapshot_count=row["snapshot_count"],
                latest_readiness=latest_readiness,
                latest_created_at=latest_created_at,
            ))
        return summaries
