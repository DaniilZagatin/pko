"""Таксономия объектов управления — обзорная страница одной версии.

Повторяет структуру `taxonomy_v1_1.html`: потребность, клиентские пути,
автономные процессы, BBB, атомарные операции, guardrails. Раздел ролей заменён
на пробелы анализа: назначать владельцев по коду PKO не имеет права.
"""

from __future__ import annotations

from pko.model.schema import PkoModel, PkoObject
from pko.render.base import esc, field_html, gaps_section, meta_bar, origin_badge, page, tag

_SECTION_STYLES = {
    "NEED": ("var(--accent-light)", "var(--accent)"),
    "JOURNEY": ("var(--green-light)", "var(--green)"),
    "PROCESS": ("var(--purple-light)", "var(--purple)"),
    "BBB": ("var(--amber-light)", "var(--amber)"),
    "AO": ("var(--cyan-light)", "var(--cyan)"),
    "GUARDRAIL": ("var(--pink-light)", "var(--pink)"),
}

_SECTION_TITLES = {
    "NEED": "Потребность клиента",
    "JOURNEY": "Клиентские пути",
    "PROCESS": "Автономные процессы",
    "BBB": "BBB — Business Building Blocks",
    "AO": "Атомарные операции",
    "GUARDRAIL": "Guardrails (ограничения исполнения)",
}


def render_taxonomy(model: PkoModel, summary: str = "") -> str:
    body = [meta_bar(model)]
    if summary:
        body.append(_summary_block(summary))

    for i, kind in enumerate(("NEED", "JOURNEY", "PROCESS", "BBB", "AO", "GUARDRAIL"), start=1):
        objects = model.by_kind(kind)
        body.append(_section(str(i), kind, objects))

    body.append(gaps_section(model, "7"))

    meta = model.meta
    return page(
        title="Таксономия объектов управления",
        subtitle=f"{meta.get('repo', '')} · восстановлено по коду",
        badge=f"Стандарт автономного процесса v1.1 · версия {esc(meta.get('version_label', ''))}",
        body="\n".join(body),
        footer=(
            f"Сформировано PKO по коммиту {esc(str(meta.get('commit', ''))[:8])} · "
            f"фактов: {model.facts_count} · доля неустановленных полей: "
            f"{model.unknown_ratio():.0%}"
        ),
    )


def _summary_block(summary: str) -> str:
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--accent-light);color:var(--accent);">≡</div>
      <h2>Краткий обзор</h2>
    </div>
    <div class="section-body"><div style="font-size:0.92rem;">{esc(summary)}</div></div>
  </div>
"""


def _section(num: str, kind: str, objects: list[PkoObject]) -> str:
    bg, fg = _SECTION_STYLES[kind]
    title = _SECTION_TITLES[kind]
    if not objects:
        inner = '<div class="desc-cell">Объекты этого типа в коде не обнаружены.</div>'
    else:
        inner = _table(objects)
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:{bg};color:{fg};">{esc(num)}</div>
      <h2>{esc(title)}</h2>
      <span class="count">{len(objects)}</span>
    </div>
    <div class="section-body">{inner}</div>
  </div>
"""


def _table(objects: list[PkoObject]) -> str:
    rows = []
    for obj in objects:
        # В обзор выносим два первых содержательных поля, остальное — в паспортах.
        preview = [
            f"<strong>{esc(label)}:</strong> {field_html(fld, max_evidence=1)}"
            for label, fld in list(obj.fields.items())[:3]
        ]
        links = " ".join(
            tag(t, _kind_of_id(t)) for targets in obj.links.values() for t in targets
            if not t.startswith(("backend", "src", "frontend", "app"))
        )
        rows.append(
            f"<tr><td class='id-cell'>{esc(obj.id)}</td>"
            f"<td>{esc(obj.name)}</td>"
            f"<td class='desc-cell'>{'<br>'.join(preview)}</td>"
            f"<td>{links}</td></tr>"
        )
    return (
        "<table class='obj-table'><thead><tr>"
        "<th>ID</th><th>Название</th><th>Основные поля</th><th>Связи</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _kind_of_id(obj_id: str) -> str:
    if obj_id.startswith("NEED"):
        return "NEED"
    if obj_id.startswith("CP-"):
        return "JOURNEY"
    if obj_id.startswith("AP-"):
        return "PROCESS"
    if obj_id.startswith("BBB"):
        return "BBB"
    if obj_id.startswith("AO"):
        return "AO"
    return "GUARDRAIL"
