"""Общие части HTML-отчётов.

Оформление повторяет уже принятый в комплекте вид (`taxonomy_v1_1.html`,
`passports_v1_1.html`): те же цветовые переменные, карточки, таблицы и теги
объектов. Отчёт остаётся одним самодостаточным файлом без внешних ресурсов.

Дополнение к исходному оформлению — бейдж происхождения у каждого поля: читатель
должен видеть, что перед ним, наблюдение в коде или неподтверждённое допущение.
"""

from __future__ import annotations

import html
import re
from typing import Any

from pko.model.schema import Field, PkoModel

ORIGIN_LABELS = {
    "VERIFIED": ("подтверждено", "green"),
    "OBSERVED": ("найдено в коде", "accent"),
    "DECLARED": ("заявлено владельцем", "purple"),
    "INFERRED": ("вывод агента", "amber"),
    "MANUAL_OVERRIDE": ("ручная правка", "amber"),
    "UNKNOWN": ("не установлено", "red"),
}

KIND_TAGS = {
    "NEED": "tag-need",
    "JOURNEY": "tag-cp",
    "PROCESS": "tag-ap",
    "BBB": "tag-bbb",
    "AO": "tag-ao",
    "GUARDRAIL": "tag-grd",
}

CSS = """
:root {
  --bg: #f8f9fb; --surface: #ffffff; --border: #e2e5ea;
  --text: #1e2229; --text-secondary: #5e6879;
  --accent: #2563eb; --accent-light: #eff4ff;
  --green: #059669; --green-light: #ecfdf5;
  --amber: #d97706; --amber-light: #fffbeb;
  --red: #dc2626; --red-light: #fef2f2;
  --purple: #7c3aed; --purple-light: #f5f3ff;
  --cyan: #0891b2; --cyan-light: #ecfeff;
  --pink: #db2777; --pink-light: #fdf2f8;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', Roboto, sans-serif;
  background: var(--bg); color: var(--text); line-height: 1.6; padding: 2rem 1rem;
}
.container { max-width: 1200px; margin: 0 auto; }
.header { text-align: center; padding: 2rem 0 2.5rem; }
.header h1 { font-size: 1.75rem; font-weight: 700; letter-spacing: -0.02em; }
.header .subtitle { color: var(--text-secondary); margin-top: 0.5rem; font-size: 0.95rem; }
.badge {
  display: inline-block; background: var(--accent-light); color: var(--accent);
  font-size: 0.75rem; font-weight: 600; padding: 0.2rem 0.75rem; border-radius: 999px;
  margin-top: 0.5rem; letter-spacing: 0.02em;
}
.badge-warn { background: var(--amber-light); color: var(--amber); }
.badge-danger { background: var(--red-light); color: var(--red); }
.meta-bar {
  display: flex; flex-wrap: wrap; gap: 1rem; justify-content: center;
  padding: 1rem 1.5rem; background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; margin-bottom: 1.5rem; font-size: 0.85rem;
}
.meta-item { display: flex; align-items: center; gap: 0.4rem; }
.meta-label { color: var(--text-secondary); }
.meta-value { font-weight: 600; }
.section {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; margin-bottom: 1.5rem; overflow: hidden;
}
.section-header {
  padding: 1rem 1.5rem; display: flex; align-items: center; gap: 0.75rem;
  border-bottom: 1px solid var(--border);
}
.section-header .icon {
  width: 36px; height: 36px; border-radius: 8px; display: flex;
  align-items: center; justify-content: center; font-size: 1rem; flex-shrink: 0;
}
.section-header h2 { font-size: 1.05rem; font-weight: 600; flex: 1; }
.section-header .count {
  font-size: 0.8rem; color: var(--text-secondary); background: var(--bg);
  padding: 0.15rem 0.6rem; border-radius: 999px;
}
.section-body { padding: 1.5rem; }
.tag {
  display: inline-block; font-size: 0.75rem; font-weight: 600;
  padding: 0.15rem 0.5rem; border-radius: 4px; margin-right: 0.25rem;
}
.tag-need { background: var(--accent-light); color: var(--accent); }
.tag-cp { background: var(--green-light); color: var(--green); }
.tag-ap { background: var(--purple-light); color: var(--purple); }
.tag-bbb { background: var(--amber-light); color: var(--amber); }
.tag-ao { background: var(--cyan-light); color: var(--cyan); }
.tag-grd { background: var(--pink-light); color: var(--pink); }
.obj-table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
.obj-table th {
  text-align: left; padding: 0.6rem 0.75rem; font-weight: 600; font-size: 0.8rem;
  text-transform: uppercase; letter-spacing: 0.04em; color: var(--text-secondary);
  border-bottom: 2px solid var(--border);
}
.obj-table td { padding: 0.6rem 0.75rem; border-bottom: 1px solid var(--border); vertical-align: top; }
.obj-table tr:last-child td { border-bottom: none; }
.obj-table .id-cell { font-weight: 600; white-space: nowrap; font-size: 0.82rem; }
.obj-table .desc-cell { color: var(--text-secondary); }
.origin {
  display: inline-block; font-size: 0.68rem; font-weight: 700; letter-spacing: 0.03em;
  padding: 0.05rem 0.4rem; border-radius: 4px; margin-left: 0.4rem; white-space: nowrap;
}
.origin-accent { background: var(--accent-light); color: var(--accent); }
.origin-green { background: var(--green-light); color: var(--green); }
.origin-amber { background: var(--amber-light); color: var(--amber); }
.origin-red { background: var(--red-light); color: var(--red); }
.origin-purple { background: var(--purple-light); color: var(--purple); }
.evidence { display: block; margin-top: 0.35rem; font-size: 0.78rem; color: var(--text-secondary); }
.evidence code { background: var(--bg); padding: 0.05rem 0.3rem; border-radius: 3px; }
.evidence-row { padding: 0.1rem 0; }
.evidence-where {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.72rem;
  color: var(--text-muted, #98a2b3); white-space: nowrap;
}
.evidence-more { font-style: italic; opacity: 0.8; }
/* Картотека: сетка карточек и страница одного паспорта. Повторяет вид
   `passports_v1_1.html` — обзор из коротких карточек, подробности по клику. */
.section-title {
  font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: var(--text-secondary); margin: 1.75rem 0 0.75rem;
}
.grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
  gap: 0.75rem;
}
.card {
  background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 0.9rem 1rem; cursor: pointer; transition: border-color 0.15s, transform 0.15s;
}
.card:hover { border-color: var(--accent); transform: translateY(-1px); }
.card .ctitle { font-weight: 600; font-size: 0.92rem; line-height: 1.35; }
.card .cid {
  display: inline-block; font-size: 0.7rem; font-weight: 700; color: var(--text-secondary);
  margin-top: 0.2rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.card .cdesc { color: var(--text-secondary); font-size: 0.83rem; margin-top: 0.4rem; }
.card .cmeta { font-size: 0.72rem; color: var(--text-secondary); margin-top: 0.5rem; }
.page { display: none; }
.page.active { display: block; }
.page-bar { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 1rem; font-size: 0.85rem; }
.page-bar .back { color: var(--accent); cursor: pointer; text-decoration: none; }
.page-bar .pbc { color: var(--text-secondary); font-family: ui-monospace, Menlo, monospace; }
.ptable { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
.ptable td { padding: 0.6rem 0.8rem; border-bottom: 1px solid var(--border); vertical-align: top; }
.ptable tr:last-child td { border-bottom: none; }
.ptable .pl { width: 34%; color: var(--text-secondary); font-weight: 500; }
.hidden { display: none; }
/* Компактные списки обзора: блок, операция, ограничение — по одной строке. */
.bbb-list, .ao-list, .grd-list { display: flex; flex-direction: column; gap: 0.5rem; }
.bbb-item, .ao-item, .grd-item {
  display: flex; align-items: flex-start; gap: 0.75rem;
  padding: 0.6rem 0.85rem; background: var(--bg); border-radius: 8px; font-size: 0.88rem;
}
.bbb-item .bbb-id, .ao-item .ao-id, .grd-item .grd-id {
  font-weight: 700; font-size: 0.8rem; white-space: nowrap;
}
.bbb-item .bbb-id { color: var(--amber); }
.ao-item .ao-id { color: var(--cyan); }
.grd-item .grd-id { color: var(--pink); }
.bbb-item .bbb-info, .ao-item .ao-info, .grd-item .grd-info { flex: 1; }
.bbb-item .bbb-name, .ao-item .ao-name, .grd-item .grd-name { font-weight: 600; }
.bbb-item .bbb-desc, .ao-item .ao-desc, .grd-item .grd-desc {
  color: var(--text-secondary); font-size: 0.85rem; margin-top: 0.1rem;
}
.side-note { font-size: 0.75rem; color: var(--text-secondary); white-space: nowrap; }
.run-notes {
  margin-top: 1rem; font-size: 0.78rem; color: var(--text-secondary); line-height: 1.5;
}
.gap-list { display: flex; flex-direction: column; gap: 0.5rem; }
.gap-item {
  padding: 0.6rem 0.9rem; background: var(--amber-light); border-radius: 8px;
  font-size: 0.85rem; color: #7c4a03;
}
.footer {
  text-align: center; color: var(--text-secondary); font-size: 0.8rem;
  padding: 2rem 0 1rem; border-top: 1px solid var(--border); margin-top: 0.5rem;
}
@media (max-width: 768px) {
  .obj-table th, .obj-table td { padding: 0.5rem; }
  .meta-bar { flex-direction: column; align-items: center; }
}
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def origin_badge(origin: str) -> str:
    label, color = ORIGIN_LABELS.get(origin, ("?", "red"))
    return f'<span class="origin origin-{color}">{esc(label)}</span>'


def tag(obj_id: str, kind: str) -> str:
    return f'<span class="tag {KIND_TAGS.get(kind, "tag-need")}">{esc(obj_id)}</span>'


def compact_value(fld: Field) -> str:
    """Значение для обзора: смысл без доказательств.

    В обзорной таблице читателю нужно понять, что за объект перед ним, а не
    сверять строки кода: блоки доказательств раздували таксономию вдвое и
    заслоняли собственно значение. Бейдж происхождения ставим, только если
    оно не `OBSERVED`: «найдено в коде» — это норма отчёта, и помечать норму
    значит помечать всё. Заметным остаётся ровно необычное: заявлено
    владельцем, выведено агентом, не установлено.
    """
    text = esc(fld.text())
    if fld.origin == "OBSERVED":
        return text
    return text + origin_badge(fld.origin)


def field_html(fld: Field, max_evidence: int = 3) -> str:
    """Значение поля, бейдж происхождения и доказательство — словами.

    Раньше доказательство печаталось как `path:line@commit`: читателю
    предъявляли координату и прятали смысл, хотя основание («эндпоинт
    POST /tasks») лежит рядом в той же записи. Теперь видно основание, а
    координата идёт следом мелким шрифтом — сверить по ней по-прежнему можно.
    """
    parts = [esc(fld.text()), origin_badge(fld.origin)]
    if fld.evidence:
        shown = fld.evidence[:max_evidence]
        rows = "".join(_evidence_row(ev) for ev in shown)
        rest = len(fld.evidence) - max_evidence
        more = f'<div class="evidence-more">и ещё {rest} подтверждени(я)</div>' if rest > 0 else ""
        parts.append(f'<div class="evidence">{rows}{more}</div>')
    return "".join(parts)


def _evidence_row(ev) -> str:
    """Одна строка доказательства: сначала объяснение, потом место."""
    where = esc(ev.where())
    explanation = esc(ev.explain())
    place = f'<span class="evidence-where" title="{where}">{where}</span>'
    if explanation:
        return f'<div class="evidence-row">{explanation} {place}</div>'
    return f'<div class="evidence-row">{place}</div>'


def page(title: str, subtitle: str, badge: str, body: str, footer: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{esc(title)}</h1>
    <div class="subtitle">{esc(subtitle)}</div>
    <div class="badge">{badge}</div>
  </div>
{body}
  <div class="footer">{footer}</div>
</div>
</body>
</html>
"""


def meta_bar(model: PkoModel) -> str:
    meta = model.meta
    items = [
        ("Репозиторий", meta.get("repo", "—")),
        ("Ветка", meta.get("branch", "—")),
        ("Версия", meta.get("version_label", "—")),
        ("Коммит", str(meta.get("commit", ""))[:8] or "—"),
        ("Дата коммита", meta.get("commit_date", "—")),
        ("Покрытие анализа", f"{model.coverage.ratio:.0%}"),
    ]
    cells = "".join(
        f'<div class="meta-item"><span class="meta-label">{esc(k)}:</span>'
        f'<span class="meta-value">{esc(v)}</span></div>'
        for k, v in items
    )
    return f'<div class="meta-bar">{cells}</div>'


# Строка предупреждения валидатора: `WARN [GRD-001] CODE: текст`. Пять
# одинаковых строк по одной на каждое ограничение вытесняли из раздела то,
# что действительно требует внимания.
_WARN_LINE = re.compile(r"^WARN \[(?P<obj>[^\]]+)\]\s*(?P<code>[A-Z_]+):\s*(?P<text>.+)$")

# Заметки о самом прогоне: сколько фактов принял агент, какие паки подключены.
# Это не пробел анализируемой системы, а сведения о работе инструмента, и в
# разделе про пробелы они сбивают с толку.
_RUN_NOTE = re.compile(r"^(Агент|Модель|Текст модели|Пояснения)[:\s]")


def split_gaps(gaps: list[str]) -> tuple[list[str], list[str]]:
    """Разделить на пробелы анализа и заметки прогона, свернув повторы."""
    grouped: dict[tuple[str, str], list[str]] = {}
    order: list[object] = []
    run_notes: list[str] = []

    for gap in gaps:
        if _RUN_NOTE.match(gap):
            run_notes.append(gap)
            continue
        match = _WARN_LINE.match(gap)
        if match:
            key = (match.group("code"), match.group("text"))
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append(match.group("obj"))
            continue
        order.append(gap)

    out: list[str] = []
    for item in order:
        if isinstance(item, str):
            out.append(item)
            continue
        objects = grouped[item]
        code, text = item
        where = ", ".join(objects)
        out.append(f"{text} ({code}): {where}" if len(objects) > 1
                   else f"{text} ({code}): {where}")
    return out, run_notes


def gaps_section(model: PkoModel, icon_num: str = "!") -> str:
    gaps, run_notes = split_gaps(model.gaps)
    if not gaps and not run_notes:
        return ""
    items = "".join(f'<div class="gap-item">{esc(g)}</div>' for g in gaps)
    notes = ""
    if run_notes:
        notes = ('<div class="run-notes">О прогоне: '
                 + " · ".join(esc(n) for n in run_notes) + "</div>")
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--amber-light);color:var(--amber);">{esc(icon_num)}</div>
      <h2>Пробелы и ограничения анализа</h2>
      <span class="count">{len(gaps)}</span>
    </div>
    <div class="section-body"><div class="gap-list">{items}</div>{notes}</div>
  </div>
"""
