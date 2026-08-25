#!/bin/bash
# Создаёт репозиторий другого стека: интерфейс на React, команда CLI, обработчик
# очереди и запись в файл. Ни FastAPI, ни LangGraph, ни SQL здесь нет.
#
# Нужен ровно для того, чтобы слепота к чужому стеку проявлялась на прогоне, а
# не оставалась предположением: статический разбор PKO не читает JS/TS, и по
# этим файлам единственный источник — агент.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO="$ROOT/fixtures/multistack_repo"

rm -rf "$REPO"
mkdir -p "$REPO/ui/src" "$REPO/cli" "$REPO/workers" "$REPO/store"
cd "$REPO"

git init -q -b master
git config user.email "pko@example.local"
git config user.name "PKO Fixture"

cat > package.json <<'EOF'
{
  "name": "orders-ui",
  "version": "1.0.0",
  "dependencies": {
    "react": "^18.2.0",
    "axios": "^1.6.0"
  }
}
EOF

cat > ui/src/OrderForm.jsx <<'EOF'
import React, { useState } from "react";
import axios from "axios";

const MAX_ITEMS = 20;

export function OrderForm() {
  const [items, setItems] = useState([]);

  const submitOrder = async () => {
    await axios.post("/api/orders", { items });
  };

  return <button onClick={submitOrder}>Оформить заказ</button>;
}
EOF

cat > cli/main.py <<'EOF'
"""Командная строка обработки заказов."""

import argparse

REQUEST_TIMEOUT = 30


def build_parser():
    parser = argparse.ArgumentParser(description="Обработка заказов")
    parser.add_argument("--batch", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main():
    args = build_parser().parse_args()
    return args.batch
EOF

cat > workers/consumer.py <<'EOF'
"""Обработчик очереди заказов."""

MAX_RETRIES = 3


class OrderConsumer:
    def __init__(self, broker):
        self.broker = broker

    def handle(self, message):
        self.broker.send("orders.processed", message)

    def listen(self):
        for message in self.broker.consume("orders.new"):
            self.handle(message)
EOF

cat > store/files.py <<'EOF'
"""Выгрузка отчётов на диск."""

from pathlib import Path

EXPORT_LIMIT = 5000


def export(rows, target):
    path = Path(target)
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(f"{row}\n")
    return path
EOF

cat > business_intent.yaml <<'EOF'
need_source: "Интервью с операционным отделом"
client: "Оператор склада"
business_owner: "Руководитель операций"
target_state: "Заказы обрабатываются пакетами без ручного ввода"
success_criteria: "Пакет из 100 заказов обработан без ошибок"
consequence_class: "reversible"
process_maturity: "pilot"
requested_mode: "ASSIST"
EOF

git add -A
git commit -qm "Интерфейс заказов, командная строка, обработчик очереди и выгрузка"
