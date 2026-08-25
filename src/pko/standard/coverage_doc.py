"""Генерация `docs/standard_coverage.md` из машинного каталога требований.

Документ о покрытии стандарта нельзя вести руками: он немедленно разойдётся с
кодом, и читатель будет думать, что PKO проверяет то, чего не проверяет.
Поэтому таблица собирается из `pko.standard.catalog`, а файл перегенерируется
командой `make coverage-doc`.

    python3 -m pko.standard.coverage_doc          # напечатать
    python3 -m pko.standard.coverage_doc --write  # записать docs/standard_coverage.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pko.standard import catalog

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "docs" / "standard_coverage.md"

_STATE_MEANING = {
    catalog.CHECKED: "проверяется детерминированно, результат влияет на решение Gate",
    catalog.PARTIAL: "проверяется частично; граница названа в колонке «Источник / ограничение»",
    catalog.NOT_CHECKED: "проверить статически можно, но проверка не реализована",
    catalog.NEEDS_RUNTIME: "по коду не доказывается: нужен исполняющий контур",
}


def render() -> str:
    lines = [
        "# Покрытие стандарта: что PKO проверяет, а что нет",
        "",
        "Файл собран из `src/pko/standard/catalog.py` — машинного каталога требований.",
        "Вести его руками нельзя: он немедленно разойдётся с кодом, и читатель решит,",
        "что PKO проверяет то, чего не проверяет. После правки каталога выполните",
        "`make coverage-doc`.",
        "",
        "Состояний четыре, и разница между двумя последними существенна.",
        "",
        "| Состояние | Что означает |",
        "|---|---|",
    ]
    for state in catalog.STATES:
        lines.append(f"| `{state}` | {_STATE_MEANING[state]} |")
    lines += [
        "",
        "`NOT_CHECKED` — это работа, которую можно сделать. `NEEDS_RUNTIME` — граница",
        "подхода: сколько бы PKO ни улучшали, наличие журнала исполнения статический",
        "разбор не покажет.",
        "",
        "## Сводка",
        "",
        "| Состояние | Требований |",
        "|---|---|",
    ]
    summary = catalog.coverage(catalog.FULL)
    for state in catalog.STATES:
        lines.append(f"| `{state}` | {summary.by_state[state]} |")
    lines += [
        "",
        f"Всего требований в каталоге: {len(catalog.REQUIREMENTS)}.",
        "",
        "## Требования",
        "",
        "| ID | § | Требование | Профиль | Область | Состояние | Источник / ограничение |",
        "|---|---|---|---|---|---|---|",
    ]
    for requirement in catalog.REQUIREMENTS:
        note = requirement.limitation or requirement.source or "—"
        lines.append(
            f"| `{requirement.id}` | {requirement.section} | {requirement.title} "
            f"| {requirement.profile} | {requirement.area} | `{requirement.state}` | {note} |"
        )

    lines += [
        "",
        "## Что мешает промышленному запуску",
        "",
        "Требования профиля FULL, которые сейчас не выполняются:",
        "",
    ]
    for requirement in catalog.blocking_for_full():
        lines.append(
            f"- `{requirement.id}` (§{requirement.section}) — {requirement.title}: "
            f"`{requirement.state}`"
        )

    lines += [
        "",
        "Что для этого нужно от исполняющего контура, описано отдельно в",
        "[`runtime_poc.md`](runtime_poc.md). Ничего из того документа PKO сегодня не",
        "делает.",
        "",
        "## Как перегенерировать",
        "",
        "```bash",
        "make coverage-doc",
        "```",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Сгенерировать документ о покрытии стандарта")
    parser.add_argument("--write", action="store_true", help="записать docs/standard_coverage.md")
    parser.add_argument("--path", default=str(DEFAULT_PATH))
    args = parser.parse_args(argv)

    text = render()
    if not args.write:
        sys.stdout.write(text)
        return 0
    target = Path(args.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    print(f"Записано: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
