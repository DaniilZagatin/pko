"""Общие части HTML-отчётов.

Оформление повторяет уже принятый в комплекте вид (`taxonomy_v1_1.html`,
`passports_v1_1.html`): те же цветовые переменные, карточки, таблицы и теги
объектов. Отчёт остаётся одним самодостаточным файлом без внешних ресурсов.

Дополнение к исходному оформлению — бейдж происхождения у каждого поля: читатель
должен видеть, что перед ним, наблюдение в коде или неподтверждённое допущение.
"""

from __future__ import annotations

import html
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
.evidence { display: block; margin-top: 0.25rem; font-size: 0.75rem; color: var(--text-secondary); }
.evidence code { background: var(--bg); padding: 0.05rem 0.3rem; border-radius: 3px; }
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


def field_html(fld: Field, max_evidence: int = 3) -> str:
    """Значение поля + бейдж происхождения + ссылки на факты (без текста кода)."""
    parts = [esc(fld.text()), origin_badge(fld.origin)]
    if fld.evidence:
        refs = " · ".join(f"<code>{esc(ev.ref())}</code>" for ev in fld.evidence[:max_evidence])
        more = f" (+{len(fld.evidence) - max_evidence})" if len(fld.evidence) > max_evidence else ""
        parts.append(f'<span class="evidence">{refs}{more}</span>')
    return "".join(parts)


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


def gaps_section(model: PkoModel, icon_num: str = "!") -> str:
    if not model.gaps:
        return ""
    items = "".join(f'<div class="gap-item">{esc(g)}</div>' for g in model.gaps)
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--amber-light);color:var(--amber);">{esc(icon_num)}</div>
      <h2>Пробелы и ограничения анализа</h2>
      <span class="count">{len(model.gaps)}</span>
    </div>
    <div class="section-body"><div class="gap-list">{items}</div></div>
  </div>
"""
