"""Markdown-сравнение двух версий — формат `diff_v1_v2.md`."""

from __future__ import annotations

from pko.diff.engine import ADDED, CHANGED, REMOVED, ModelDiff

_KIND_TITLES = (
    ("NEED", "Потребность клиента"),
    ("JOURNEY", "Клиентские пути"),
    ("PROCESS", "Автономные процессы"),
    ("BBB", "BBB — Business Building Blocks"),
    ("AO", "Атомарные операции"),
    ("GUARDRAIL", "Guardrails"),
)

_MARK = {ADDED: "**+ НОВЫЙ**", REMOVED: "**− УДАЛЁН**", CHANGED: "изменён", "SAME": "—"}


def render_diff_md(diff: ModelDiff, narrative: str = "") -> str:
    lines: list[str] = []
    add = lines.append

    left_commit = str(diff.left_meta.get("commit", ""))[:8]
    right_commit = str(diff.right_meta.get("commit", ""))[:8]

    add(f"# Diff: {diff.left_label} → {diff.right_label}")
    add("")
    add("> **Стандарт:** Автономный процесс v1.1  ")
    add(f"> **Репозиторий:** `{diff.right_meta.get('repo', '')}`  ")
    add(f"> **Версии:** `{left_commit}` ({diff.left_meta.get('commit_date', '')}) → "
        f"`{right_commit}` ({diff.right_meta.get('commit_date', '')})")
    add("")
    add("---")
    add("")

    if narrative:
        add("## Общая оценка")
        add("")
        add(narrative)
        add("")
        add("---")
        add("")

    add("## Объекты управления")
    add("")
    add(f"| Тип | {diff.left_label} | {diff.right_label} | Diff |")
    add("|---|---|---|---|")
    for kind, title in _KIND_TITLES:
        before = diff.counts_left.get(kind, 0)
        after = diff.counts_right.get(kind, 0)
        delta = after - before
        mark = "—" if delta == 0 else (f"**+ {delta}**" if delta > 0 else f"**− {abs(delta)}**")
        add(f"| {title} | {before} | {after} | {mark} |")
    add("")

    summary = diff.summary()
    add(f"Добавлено объектов: **{summary[ADDED]}** · удалено: **{summary[REMOVED]}** · "
        f"изменено: **{summary[CHANGED]}**")
    add("")
    add("---")
    add("")

    for kind, title in _KIND_TITLES:
        items = [o for o in diff.by_kind(kind) if o.status != "SAME"]
        if not items:
            continue
        add(f"## {title}")
        add("")
        for obj in items:
            add(f"### {obj.id} — {obj.name} · {_MARK.get(obj.status, obj.status)}")
            add("")
            if obj.changes:
                add("| Поле | Было | Стало |")
                add("|---|---|---|")
                for ch in obj.changes:
                    add(f"| {ch.label} | {_cell(ch.before)} | {_cell(ch.after)} |")
                add("")
            elif obj.status == ADDED:
                add("Объект появился в этой версии.")
                add("")
            elif obj.status == REMOVED:
                add("Объект отсутствует в новой версии.")
                add("")
    unchanged = [o for o in diff.objects if o.status == "SAME"]
    if unchanged:
        add("## Без изменений")
        add("")
        add(", ".join(f"`{o.id}`" for o in unchanged))
        add("")

    add("---")
    add("")
    add("*Все счётчики и статусы вычислены сравнением моделей, а не пересказом текста.*")
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    flat = " ".join(str(text).split())
    if len(flat) > 160:
        flat = flat[:157] + "…"
    return flat.replace("|", "\\|") or "—"
