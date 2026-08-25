"""Таксономия объектов управления — обзорная страница одной версии.

Повторяет структуру `taxonomy_v1_1.html`: потребность, клиентские пути,
автономные процессы, BBB, атомарные операции, guardrails. Раздел ролей заменён
на пробелы анализа: назначать владельцев по коду PKO не имеет права.

Обзор отвечает на вопрос «что здесь есть», а не «чем это доказано». Поэтому у
каждого типа объектов свои короткие колонки, а доказательства (`path:line` и
основание) остаются в паспорте: в обзорной таблице они занимали больше места,
чем сами значения, и читатель переставал видеть картину за ссылками.
"""

from __future__ import annotations

from pko.model.schema import REFERENCE_LINKS, PkoModel, PkoObject
from pko.render.base import compact_value, esc, gaps_section, meta_bar, page, tag

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

# Колонки обзора для каждого типа: заголовок и поле модели. Набор повторяет
# эталон — ровно то, что нужно, чтобы отличить один объект от другого.
_COLUMNS = {
    "JOURNEY": (("Потребность", "Потребность"), ("Целевое состояние", "Целевое состояние"),
                ("Критерий результата", "Критерии результата")),
    "PROCESS": (("Клиентский путь", "Связанный клиентский путь"),
                ("Логика сборки", "Правила сборки траектории"),
                ("Условия запуска", "Условия запуска")),
}

# Однострочный вид: что показать справа от названия.
_LIST_META = {
    "BBB": "Способы исполнения",
    "AO": "Механизм",
    "GUARDRAIL": "Severity",
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
            f"{model.unknown_ratio():.0%} · значения без пометки найдены в коде, "
            f"подробности и доказательства — в паспортах"
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
    elif kind == "NEED":
        inner = "".join(_need_grid(obj) for obj in objects)
    elif kind in _COLUMNS:
        inner = _table(kind, objects)
    else:
        inner = _list(kind, objects)
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


def _need_grid(obj: PkoObject) -> str:
    """Потребность одна, поэтому показываем её парами «поле — значение»."""
    rows = [f"<tr><td class='pl'>ID</td><td class='id-cell'>{esc(obj.id)}</td></tr>",
            f"<tr><td class='pl'>Название</td><td>{esc(obj.name)}</td></tr>"]
    rows.extend(
        f"<tr><td class='pl'>{esc(label)}</td><td>{compact_value(fld)}</td></tr>"
        for label, fld in obj.fields.items()
    )
    return f"<table class='ptable'>{''.join(rows)}</table>"


def _table(kind: str, objects: list[PkoObject]) -> str:
    columns = _COLUMNS[kind]
    head = "".join(f"<th>{esc(title)}</th>" for title, _ in columns)
    rows = []
    for obj in objects:
        cells = []
        for _title, label in columns:
            field = obj.fields.get(label)
            cells.append(f"<td class='desc-cell'>{compact_value(field) if field else '—'}</td>")
        rows.append(
            f"<tr><td class='id-cell'>{esc(obj.id)}</td>"
            f"<td>{esc(obj.name)}</td>{''.join(cells)}"
            f"<td class='desc-cell'>{_links_summary(obj)}</td></tr>"
        )
    return (
        f"<table class='obj-table'><thead><tr><th>ID</th><th>Название</th>{head}"
        "<th>Связи</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
    )


def _list(kind: str, objects: list[PkoObject]) -> str:
    """Блоки, операции и ограничения — по одной строке на объект."""
    prefix = {"BBB": "bbb", "AO": "ao", "GUARDRAIL": "grd"}[kind]
    items = []
    for obj in objects:
        meta_label = _LIST_META.get(kind, "")
        field = obj.fields.get(meta_label) if meta_label else None
        meta = ""
        if field is not None and field.origin != "UNKNOWN" and field.text():
            meta = f'<span class="side-note">{esc(field.text().split(" — ")[0])}</span>'
        items.append(
            f'<div class="{prefix}-item">'
            f'<span class="{prefix}-id">{esc(obj.id)}</span>'
            f'<div class="{prefix}-info">'
            f'<div class="{prefix}-name">{esc(obj.name)}</div>'
            f'<div class="{prefix}-desc">{esc(_one_line(obj))}</div>'
            f"</div>{meta}</div>"
        )
    return f'<div class="{prefix}-list">{"".join(items)}</div>'


def _one_line(obj: PkoObject) -> str:
    """Короткое пояснение под названием: первое поле, добавляющее новое."""
    name = " ".join(obj.name.split()).lower()
    for label, field in obj.fields.items():
        if field.origin == "UNKNOWN" or not field.text():
            continue
        value = " ".join(field.text().split())
        if value.lower() == name or value.lower() in name:
            continue
        return _short(f"{label}: {value}", 110)
    return "описание из кода не восстановлено"


def _links_summary(obj: PkoObject) -> str:
    """Счётчик связей вместо россыпи тегов.

    У процесса связей бывает полтора десятка: девять блоков и пять ограничений.
    Перечень тегов занимал больше места, чем всё остальное в строке, и ничего
    не сообщал сверх количества — подробности читатель смотрит в паспорте.
    """
    counts: dict[str, int] = {}
    single: list[str] = []
    for rel, targets in obj.links.items():
        if rel not in REFERENCE_LINKS:
            continue
        for target in targets:
            counts[_kind_of_id(target)] = counts.get(_kind_of_id(target), 0) + 1
            single.append(target)
    if not counts:
        return "—"
    if len(single) <= 2:
        return " ".join(tag(t, _kind_of_id(t)) for t in single)
    names = {"NEED": "потребность", "JOURNEY": "клиентских путей", "PROCESS": "процессов",
             "BBB": "BBB", "AO": "операций", "GUARDRAIL": "guardrails"}
    return " · ".join(f"{n} {names.get(kind, kind)}" for kind, n in sorted(counts.items()))


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


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
