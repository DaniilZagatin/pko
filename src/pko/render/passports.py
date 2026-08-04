"""Паспорта объектов управления.

Паспорт здесь — представление, а не документ (§4.0): он целиком собирается из
модели. У каждой строки видно происхождение значения и ссылка на факт вида
`path:line@commit`. Навигация сделана якорями, без внешних библиотек, чтобы файл
открывался и печатался в любом окружении.
"""

from __future__ import annotations

from pko.model.schema import PkoModel, PkoObject
from pko.render.base import esc, field_html, gaps_section, meta_bar, page, tag

_NAV_CSS = """
.layout { display: grid; grid-template-columns: 260px 1fr; gap: 1.5rem; align-items: start; }
.nav {
  position: sticky; top: 1rem; background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 1rem; font-size: 0.85rem; max-height: 90vh; overflow: auto;
}
.nav h3 {
  font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--text-secondary); margin: 0.9rem 0 0.35rem;
}
.nav h3:first-child { margin-top: 0; }
.nav a { display: block; color: var(--text); text-decoration: none; padding: 0.15rem 0; }
.nav a:hover { color: var(--accent); }
.passport { scroll-margin-top: 1rem; }
.passport-head { display: flex; align-items: center; gap: 0.6rem; }
.kv { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
.kv th {
  text-align: left; width: 34%; padding: 0.55rem 0.75rem; color: var(--text-secondary);
  font-weight: 500; vertical-align: top; border-bottom: 1px solid var(--border);
}
.kv td { padding: 0.55rem 0.75rem; border-bottom: 1px solid var(--border); }
.kv tr:last-child th, .kv tr:last-child td { border-bottom: none; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } .nav { position: static; } }
"""

_GROUPS = (
    ("NEED", "Потребность"),
    ("JOURNEY", "Клиентские пути"),
    ("PROCESS", "Автономные процессы"),
    ("BBB", "BBB"),
    ("AO", "Атомарные операции"),
    ("GUARDRAIL", "Guardrails"),
)


def render_passports(model: PkoModel, notes: dict[str, str] | None = None) -> str:
    notes = notes or {}
    nav_parts: list[str] = []
    body_parts: list[str] = []

    for kind, title in _GROUPS:
        objects = model.by_kind(kind)
        if not objects:
            continue
        nav_parts.append(f"<h3>{esc(title)}</h3>")
        nav_parts.extend(
            f'<a href="#{esc(o.id)}">{esc(o.id)} — {esc(_short(o.name))}</a>' for o in objects
        )
        body_parts.extend(_passport(o, notes.get(o.id, "")) for o in objects)

    layout = f"""
  <div class="layout">
    <div class="nav">{''.join(nav_parts)}</div>
    <div>{''.join(body_parts)}{gaps_section(model, "!")}</div>
  </div>
"""
    meta = model.meta
    html_doc = page(
        title="Паспорта объектов управления",
        subtitle=f"{meta.get('repo', '')} · сгенерировано из реализации",
        badge=f"Стандарт v1.1 · версия {esc(meta.get('version_label', ''))} · "
              f"коммит {esc(str(meta.get('commit', ''))[:8])}",
        body=meta_bar(model) + layout,
        footer="Паспорт является представлением реализации: ручное ведение полей не предусмотрено",
    )
    return html_doc.replace("</style>", _NAV_CSS + "</style>")


def _passport(obj: PkoObject, note: str) -> str:
    rows = "".join(
        f"<tr><th>{esc(label)}</th><td>{field_html(fld)}</td></tr>"
        for label, fld in obj.fields.items()
    )
    links = " ".join(
        tag(t, _kind_of_id(t))
        for rel, targets in obj.links.items()
        if rel != "package"
        for t in targets
    )
    note_html = (
        f'<div class="section-body" style="padding-top:0;font-size:0.88rem;">{esc(note)}</div>'
        if note else ""
    )
    return f"""
  <div class="section passport" id="{esc(obj.id)}">
    <div class="section-header">
      <div class="passport-head">
        {tag(obj.id, obj.kind)}
        <h2>{esc(obj.name)}</h2>
      </div>
      <span class="count">{esc(obj.kind_title)}</span>
    </div>
    <div class="section-body">
      <table class="kv">{rows}</table>
      {f'<div style="margin-top:0.75rem;">Связи: {links}</div>' if links else ''}
    </div>
    {note_html}
  </div>
"""


def _short(text: str, limit: int = 34) -> str:
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
