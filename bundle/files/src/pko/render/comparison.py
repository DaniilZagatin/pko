"""HTML-сравнение версий — визуальный аналог `passports_comparison_v1_v2.html`."""

from __future__ import annotations

from pko.diff.engine import ADDED, CHANGED, REMOVED, SAME, ModelDiff
from pko.render.base import esc, page

_EXTRA_CSS = """
.status { display:inline-block; font-size:0.72rem; font-weight:700; padding:0.1rem 0.45rem; border-radius:4px; }
.st-added { background: var(--green-light); color: var(--green); }
.st-removed { background: var(--red-light); color: var(--red); }
.st-changed { background: var(--amber-light); color: var(--amber); }
.st-same { background: var(--bg); color: var(--text-secondary); }
.cmp { width:100%; border-collapse:collapse; font-size:0.85rem; }
.cmp th { text-align:left; padding:0.5rem 0.7rem; color:var(--text-secondary); font-size:0.75rem;
  text-transform:uppercase; letter-spacing:0.04em; border-bottom:2px solid var(--border); }
.cmp td { padding:0.5rem 0.7rem; border-bottom:1px solid var(--border); vertical-align:top; }
.cmp .before { color: var(--text-secondary); }
/* Две колонки версий, как в `passports_comparison_v1_v2.html`: слева было,
   справа стало. Одна таблица изменений заставляла держать обе версии в
   голове; рядом их видно сразу. */
.versions { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; align-items: start; }
.version-col h3 {
  font-size: 0.85rem; font-weight: 700; margin-bottom: 0.6rem; color: var(--text-secondary);
}
.chg { display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.5rem; }
.chg-item { font-size: 0.82rem; color: var(--text-secondary); }
@media (max-width: 900px) { .versions { grid-template-columns: 1fr; } }
"""

_KINDS = (
    ("NEED", "Потребность клиента"),
    ("JOURNEY", "Клиентские пути"),
    ("PROCESS", "Автономные процессы"),
    ("BBB", "BBB"),
    ("AO", "Атомарные операции"),
    ("GUARDRAIL", "Guardrails"),
)

_STATUS_CLASS = {ADDED: "st-added", REMOVED: "st-removed", CHANGED: "st-changed", SAME: "st-same"}
_STATUS_TEXT = {ADDED: "новый", REMOVED: "удалён", CHANGED: "изменён", SAME: "без изменений"}


def render_comparison(diff: ModelDiff, narrative: str = "") -> str:
    body: list[str] = [_counts_section(diff)]
    if narrative:
        body.insert(0, _narrative(narrative))

    for kind, title in _KINDS:
        items = [o for o in diff.by_kind(kind) if o.status != SAME]
        if not items:
            continue
        cards = "".join(_change_card(obj) for obj in items)
        body.append(
            f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--accent-light);color:var(--accent);">◆</div>
      <h2>{esc(title)}</h2><span class="count">{len(items)}</span>
    </div>
    <div class="section-body"><div class="grid">{cards}</div></div>
  </div>
"""
        )

    doc = page(
        title="Сравнение версий",
        subtitle=f"{diff.right_meta.get('repo', '')} · {diff.left_label} → {diff.right_label}",
        badge=f"{esc(str(diff.left_meta.get('commit',''))[:8])} → "
              f"{esc(str(diff.right_meta.get('commit',''))[:8])}",
        body="\n".join(body),
        footer="Сравнение выполнено по моделям объектов, а не по тексту отчётов",
    )
    return doc.replace("</style>", _EXTRA_CSS + "</style>")


def _change_card(obj) -> str:
    """Один изменившийся объект карточкой: что это и что с ним стало."""
    changes = "".join(
        f"<div class='chg-item'><strong>{esc(c.label)}</strong>: "
        f"<span class='before'>{esc(_short(c.before, 60))}</span> → {esc(_short(c.after, 60))}</div>"
        for c in obj.changes[:4]
    )
    rest = len(obj.changes) - 4
    more = f"<div class='chg-item before'>…ещё изменений: {rest}</div>" if rest > 0 else ""
    return (
        f'<div class="card" style="cursor:default;">'
        f'<div class="ctitle" title="{esc(obj.name)}">{esc(_short(obj.name, 44))}</div>'
        f'<span class="cid">{esc(obj.id)}</span> '
        f"<span class='status {_STATUS_CLASS[obj.status]}'>{_STATUS_TEXT[obj.status]}</span>"
        f'<div class="chg">{changes}{more}</div></div>'
    )


def _narrative(text: str) -> str:
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--accent-light);color:var(--accent);">≡</div>
      <h2>Общая оценка</h2>
    </div>
    <div class="section-body" style="font-size:0.92rem;">{esc(text)}</div>
  </div>
"""


def _counts_section(diff: ModelDiff) -> str:
    left, right = [], []
    for kind, title in _KINDS:
        before = diff.counts_left.get(kind, 0)
        after = diff.counts_right.get(kind, 0)
        delta = after - before
        mark = "—" if delta == 0 else (f"+{delta}" if delta > 0 else str(delta))
        cls = "st-same" if delta == 0 else ("st-added" if delta > 0 else "st-removed")
        left.append(f"<tr><td>{esc(title)}</td><td>{before}</td></tr>")
        right.append(
            f"<tr><td>{esc(title)}</td><td>{after} "
            f"<span class='status {cls}'>{esc(mark)}</span></td></tr>"
        )
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--purple-light);color:var(--purple);">Σ</div>
      <h2>Объекты управления</h2>
    </div>
    <div class="section-body">
      <div class="versions">
        <div class="version-col"><h3>{esc(diff.left_label)} — было</h3>
          <table class="cmp"><tbody>{''.join(left)}</tbody></table></div>
        <div class="version-col"><h3>{esc(diff.right_label)} — стало</h3>
          <table class="cmp"><tbody>{''.join(right)}</tbody></table></div>
      </div>
    </div>
  </div>
"""


def _short(text: str, limit: int = 90) -> str:
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
