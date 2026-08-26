"""HTML-отчёт о прогрессе: план (PPTX) сверен с кодом целевого репозитория.

В том же стиле, что и остальные отчёты PKO (`render/base.py`: `page()`, `esc()`,
общая CSS) — один самодостаточный файл без внешних ресурсов. Домен другой
(вердикт по пункту плана, а не паспорт объекта управления), поэтому вёрстка
собственная, а не переиспользование `render/passports.py`/`render/taxonomy.py`.

Проверка evidence (`verify_evidence`) и текста (`_guard_explanation`) в
`progress.matcher` по-прежнему решает, что вообще может попасть сюда —
неподтверждённая evidence в отчёт просто не проходит. Но сам факт «это было
проверено» здесь никак не показывается: ни бейджей «подтверждено/не
подтверждено», ни зачёркивания, ни причины отказа рядом со ссылкой. Читателю
показывается вердикт и то, чем он подкреплён, а не механика проверки.
"""

from __future__ import annotations

from pko.progress.schema import EvidenceRef, ItemVerdict, ProgressModel
from pko.render.base import authorship, esc, page

_STATUS_LABELS = {
    "DONE": ("Сделано", "green"),
    "PARTIAL": ("Частично", "amber"),
    "NOT_STARTED": ("Не начато", "red"),
    "UNCLEAR": ("Неясно", "purple"),
}

_STATUS_ORDER = ("DONE", "PARTIAL", "NOT_STARTED", "UNCLEAR")

_EXTRA_CSS = """
.status-badge {
  display: inline-block; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.02em;
  padding: 0.15rem 0.55rem; border-radius: 999px; margin-left: 0.5rem; white-space: nowrap;
}
.status-green { background: var(--green-light); color: var(--green); }
.status-amber { background: var(--amber-light); color: var(--amber); }
.status-red { background: var(--red-light); color: var(--red); }
.status-purple { background: var(--purple-light); color: var(--purple); }
.progress-bar-outer {
  width: 100%; height: 10px; background: var(--bg); border-radius: 999px; overflow: hidden;
  margin-top: 0.75rem;
}
.progress-bar-inner { height: 100%; background: var(--green); border-radius: 999px; }
.item-card {
  padding: 0.9rem 1rem; background: var(--bg); border-radius: 8px; margin-bottom: 0.6rem;
}
.item-card .item-title { font-weight: 600; font-size: 0.95rem; }
.item-card .item-stage {
  font-size: 0.72rem; color: var(--text-secondary); text-transform: uppercase;
  letter-spacing: 0.04em; margin-top: 0.15rem;
}
.item-card .item-explanation { font-size: 0.86rem; color: var(--text-secondary); margin-top: 0.4rem; }
.evidence-list { margin-top: 0.5rem; display: flex; flex-direction: column; gap: 0.25rem; }
.unclaimed-list { display: flex; flex-direction: column; gap: 0.4rem; }
.unclaimed-item {
  padding: 0.5rem 0.85rem; background: var(--bg); border-radius: 8px; font-size: 0.85rem;
}
.unclaimed-item .u-group { font-weight: 600; }
.unclaimed-item .u-paths {
  color: var(--text-secondary); font-size: 0.78rem; margin-top: 0.15rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
.summary-text { font-size: 0.95rem; line-height: 1.65; }
"""


def render_progress_report(model: ProgressModel) -> str:
    ratio = model.progress_ratio()
    counts = model.counts()
    title = "Отчёт о прогрессе"
    subtitle = f"{model.meta.get('repo', '—')} · план: {model.meta.get('plan_source', '—')}"
    badge = f"{ratio:.0%} пунктов плана сделано"

    body = (
        _summary_text_section(model)
        + _summary_bar(model, counts, ratio)
        + _items_by_status(model)
        + _unclaimed_section(model)
        + _gaps_section(model)
    )
    footer = (
        f"PKO progress · коммит {str(model.meta.get('commit', ''))[:8] or '—'} · "
        f"{model.meta.get('generated_at', '')}"
    )
    html = page(title=title, subtitle=subtitle, badge=badge, body=body, footer=footer)
    return html.replace("</style>", _EXTRA_CSS + "</style>")


def _summary_text_section(model: ProgressModel) -> str:
    if not model.summary:
        return ""
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--accent-light);color:var(--accent);">✦</div>
      <h2>Итог</h2>
    </div>
    <div class="section-body">
      <div class="summary-text">{esc(model.summary)}</div>
      <div class="authorship">{authorship(model.summary_source)}</div>
    </div>
  </div>
"""


def _summary_bar(model: ProgressModel, counts: dict[str, int], ratio: float) -> str:
    total = len(model.verdicts)
    items = "".join(
        f'<div class="meta-item"><span class="meta-label">{esc(label)}:</span>'
        f'<span class="meta-value">{counts.get(status, 0)}</span></div>'
        for status, (label, _color) in _STATUS_LABELS.items()
    )
    bar = (
        f'<div class="progress-bar-outer">'
        f'<div class="progress-bar-inner" style="width:{ratio * 100:.0f}%"></div></div>'
    )
    return f"""
  <div class="meta-bar">
    <div class="meta-item"><span class="meta-label">Всего пунктов:</span>
      <span class="meta-value">{total}</span></div>
    {items}
  </div>
  {bar}
"""


def _items_by_status(model: ProgressModel) -> str:
    by_status: dict[str, list[ItemVerdict]] = {s: [] for s in _STATUS_ORDER}
    for v in model.verdicts:
        by_status.setdefault(v.status, []).append(v)

    sections = []
    for status in _STATUS_ORDER:
        verdicts = by_status.get(status, [])
        if not verdicts:
            continue
        label, color = _STATUS_LABELS[status]
        cards = "".join(_item_card(v, model) for v in verdicts)
        sections.append(f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--{color}-light);color:var(--{color});">•</div>
      <h2>{esc(label)}</h2>
      <span class="count">{len(verdicts)}</span>
    </div>
    <div class="section-body">{cards}</div>
  </div>
""")
    return "".join(sections)


def _item_card(verdict: ItemVerdict, model: ProgressModel) -> str:
    item = model.items.get(verdict.item_id)
    title = item.title if item else verdict.item_id
    stage = item.stage if item else ""
    # Показываем только то, что реально прошло проверку (verify_evidence) —
    # неподтверждённая ссылка не рисуется вовсе, а не помечается как отклонённая:
    # читателю нужен результат, а не механика того, как его перепроверяли.
    evidence_html = "".join(_evidence_row(e) for e in verdict.verified_evidence)
    evidence_block = f'<div class="evidence-list">{evidence_html}</div>' if evidence_html else ""
    return f"""
    <div class="item-card">
      <div class="item-title">{esc(title)}</div>
      {f'<div class="item-stage">{esc(stage)}</div>' if stage else ""}
      <div class="item-explanation">{esc(verdict.explanation)}</div>
      {evidence_block}
    </div>
"""


def _evidence_row(ev: EvidenceRef) -> str:
    where = f"{ev.path}:{ev.line}" if ev.line else ev.path
    return (
        f'<div class="evidence-row">{esc(ev.basis)} '
        f'<span class="evidence-where">{esc(where)}</span></div>'
    )


def _unclaimed_section(model: ProgressModel) -> str:
    if not model.unclaimed:
        return ""
    items = "".join(
        f"""<div class="unclaimed-item">
          <div class="u-group">{esc(g.group)}</div>
          <div class="u-paths">{esc(', '.join(g.example_paths))}"""
        f"""{f' и ещё {g.file_count - len(g.example_paths)}' if g.file_count > len(g.example_paths) else ''}</div>
        </div>"""
        for g in model.unclaimed
    )
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--cyan-light);color:var(--cyan);">+</div>
      <h2>Возможно, сделано сверх плана</h2>
      <span class="count">{len(model.unclaimed)}</span>
    </div>
    <div class="section-body">
      <div class="unclaimed-list">{items}</div>
      <div class="authorship">Код с фактами, не процитированный ни одним подтверждённым пунктом
      плана — не вердикт, а кандидат для ручной проверки.</div>
    </div>
  </div>
"""


def _gaps_section(model: ProgressModel) -> str:
    if not model.gaps:
        return ""
    items = "".join(f'<div class="gap-item">{esc(g)}</div>' for g in model.gaps)
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--amber-light);color:var(--amber);">!</div>
      <h2>Пробелы и ограничения анализа</h2>
      <span class="count">{len(model.gaps)}</span>
    </div>
    <div class="section-body"><div class="gap-list">{items}</div></div>
  </div>
"""
