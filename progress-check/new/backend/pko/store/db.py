"""Подключение и схема SQLite-хранилища продуктов/снимков.

Путь к файлу — `PKO_DATA_DIR/store.db`, тот же паттерн переменной окружения,
что и `PKO_ASSEMBLER_*` у LLM-ролей (`llm/registry.py`). По умолчанию —
`~/.pko` (рядом с `~/.pko/llm-cache`, `llm/client.py::DEFAULT_CACHE_DIR`).

Важно при запуске `pko serve` в контейнере (Podman/Docker): домашний каталог
внутри контейнера — не персистентный слой, он пересоздаётся вместе с
контейнером. `PKO_DATA_DIR` нужно явно смонтировать как volume — иначе вся
история проверок продукта пропадёт при пересоздании контейнера. См. README.

Одна функция `connect()` на каждое обращение (а не один держащийся процесс
процесса connection) — так же просто, как остальной PKO, и не требует
`check_same_thread=False`: каждый вызов открывает и закрывает своё
соединение, WAL-режим и `busy_timeout` покрывают конкуренцию нескольких
фоновых потоков `web.analyses._execute`.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from pko.util.paths import harden_dir, harden_file

DEFAULT_DATA_DIR = Path.home() / ".pko"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_stages (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_canonical_stages_product ON canonical_stages(product_id);

CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    overall_readiness REAL NOT NULL,
    source_json TEXT NOT NULL,
    model_json TEXT NOT NULL,
    UNIQUE (product_id, version_number)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_product ON snapshots(product_id);

CREATE TABLE IF NOT EXISTS comparisons (
    product_id TEXT NOT NULL,
    from_snapshot_id TEXT NOT NULL,
    to_snapshot_id TEXT NOT NULL,
    facts_json TEXT NOT NULL,
    interpretation_json TEXT,
    PRIMARY KEY (product_id, from_snapshot_id, to_snapshot_id)
);
"""


def data_dir() -> Path:
    override = os.environ.get("PKO_DATA_DIR")
    return Path(override).expanduser() if override else DEFAULT_DATA_DIR


def db_path() -> Path:
    return data_dir() / "store.db"


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Открыть соединение и убедиться, что схема на месте.

    `executescript` с `CREATE TABLE IF NOT EXISTS` — идемпотентно, вызывается
    на каждое подключение, отдельной команды миграции для MVP не заводим:
    таблиц три-четыре, ни одна ещё не публиковалась с несовместимой формой.
    """
    target = path or db_path()
    is_new_dir = not target.parent.exists()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    conn.commit()
    if is_new_dir:
        harden_dir(target.parent)
    harden_file(target)
    return conn
