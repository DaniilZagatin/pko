#!/usr/bin/env python3
"""Сводка по прогонам бенчмарка.

Читает `bench/runs/*/*/metrics.json` и складывает их в одну markdown-таблицу.
Нужна, чтобы сравнить редакции промпта и модели постфактум: сам прогон всегда
одномодельный, сравнение — здесь.

    python3 bench/report.py            # все прогоны
    python3 bench/report.py --last 3   # три последних
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RUNS_DIR = Path(__file__).with_name("runs")

COLUMNS = (
    ("run", "Прогон"),
    ("target", "Цель"),
    ("model", "Модель"),
    ("prompt", "Промпт"),
    ("steps", "Шагов"),
    ("facts", "Факты ✓/всего"),
    ("packs", "Паки"),
    ("categories", "Категории"),
    ("mechanisms", "Механизмы"),
    ("confirmed", "В вердикте"),
    ("bbb", "BBB"),
    ("ao", "AO"),
    ("grd", "GRD"),
    ("coverage", "Покрытие"),
    ("unparsed", "Не разобрано"),
    ("unknown", "UNKNOWN"),
    ("decision", "Решение"),
    ("stop", "Остановка"),
)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    rows = _collect(RUNS_DIR, args.last)
    if not rows:
        print(f"В {RUNS_DIR} нет ни одного прогона — запустите bench/run_bench.py")
        return 1

    header = "| " + " | ".join(title for _, title in COLUMNS) + " |"
    sep = "|" + "|".join("---" for _ in COLUMNS) + "|"
    lines = [header, sep]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(key, "—")) for key, _ in COLUMNS) + " |")

    print("\n".join(lines))
    return 0


def _collect(runs_dir: Path, last: int) -> list[dict]:
    if not runs_dir.exists():
        return []
    run_dirs = sorted((p for p in runs_dir.iterdir() if p.is_dir()), reverse=True)
    if last:
        run_dirs = run_dirs[:last]

    rows: list[dict] = []
    for run_dir in sorted(run_dirs):
        for metrics_path in sorted(run_dir.glob("*/metrics.json")):
            try:
                data = json.loads(metrics_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows.append(_row(run_dir.name, data))
    return rows


# Ради чего эта колонка: универсализация мерится не числом объектов, а тем,
# сколько разных сторон системы удалось увидеть. Прогон, нашедший десять
# узлов графа и ничего больше, беднее прогона с точкой входа, шагом, эффектом
# и ограничением, хотя объектов у первого больше.
_CORE_CATEGORIES = ("ENTRYPOINT", "STEP", "EFFECT", "CONTROL")


def _row(run: str, data: dict) -> dict:
    agent = data.get("agent") or {}
    accepted = agent.get("accepted", 0)
    rejected = agent.get("rejected", 0)
    counts = data.get("counts") or {}
    semantics = data.get("semantics") or {}
    categories = semantics.get("categories") or {}
    mechanisms = semantics.get("mechanisms") or {}
    covered_core = sum(1 for name in _CORE_CATEGORIES if categories.get(name))
    unparsed = semantics.get("unparsed_files", 0)
    languages = ", ".join(semantics.get("unparsed_languages") or [])
    return {
        "run": run,
        "target": data.get("target", "—"),
        "model": data.get("model", "—"),
        "prompt": f"v{data.get('prompt_version', '?')}·{data.get('prompt_sha', '')[:6]}",
        "steps": agent.get("steps", "—"),
        "facts": f"{accepted}/{accepted + rejected}",
        "packs": ",".join(data.get("packs") or []) or "—",
        # Сколько из четырёх сторон процесса восстановлено: вход, шаг, эффект,
        # ограничение. Пустая сторона — это пробел картины, а не мелочь.
        "categories": f"{covered_core}/{len(_CORE_CATEGORIES)}",
        "mechanisms": len([m for m in mechanisms if m and m != "—"]) or "—",
        "confirmed": _share(semantics),
        "bbb": counts.get("BBB", "—"),
        "ao": counts.get("AO", "—"),
        "grd": counts.get("GUARDRAIL", "—"),
        "coverage": f"{float(data.get('coverage_ratio', 0)):.0%}",
        # Честность покрытия: сколько файлов прикладного кода статический
        # разбор не трогал вовсе. Высокое «Покрытие» при непустой этой
        # колонке означает, что считали не то.
        "unparsed": f"{unparsed} {languages}".strip() if unparsed else "—",
        "unknown": f"{float(data.get('unknown_ratio', 0)):.0%}",
        "decision": data.get("decision", "—"),
        "stop": (data.get("stop_reason") or "—")[:40],
    }


def _share(semantics: dict) -> str:
    """Доля наблюдений, на которые вердикту разрешено опираться."""
    total = semantics.get("facts") or 0
    if not total:
        return "—"
    return f"{semantics.get('gate_eligible', 0)}/{total}"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Сводная таблица по прогонам бенчмарка")
    parser.add_argument("--last", type=int, default=0, help="только N последних прогонов")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
