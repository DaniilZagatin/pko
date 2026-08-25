"""Трасса в читаемом виде.

JSON хранит всё, но искать по нему шаг с ошибкой неудобно. Здесь тот же ход
разведки разложен по шагам: видно запрос, ответ модели, вызванный инструмент и
его результат, а шаги с отброшенными фактами помечены.

Прочитанные файлы сворачиваются в `<details>`: при неограниченных шагах полный
текст сделал бы страницу неоткрываемой. Полное содержимое остаётся в JSON.
"""

from __future__ import annotations

from pko.agent.trace import Trace
from pko.render.base import esc, page

_CSS = """
.step { background: var(--surface); border: 1px solid var(--border); border-radius: 12px;
  margin-bottom: 0.75rem; overflow: hidden; }
.step-head { display: flex; align-items: center; gap: 0.6rem; padding: 0.6rem 1rem;
  border-bottom: 1px solid var(--border); font-size: 0.88rem; }
.step-num { font-weight: 700; color: var(--text-secondary); min-width: 2.5rem; }
.step-body { padding: 0.75rem 1rem; font-size: 0.85rem; }
.step-bad { border-color: var(--red); }
.step-bad .step-head { background: var(--red-light); }
.badge-tool { background: var(--accent-light); color: var(--accent); }
.badge-final { background: var(--green-light); color: var(--green); }
.badge-err { background: var(--red-light); color: var(--red); }
.chip { display:inline-block; font-size:0.72rem; font-weight:700; padding:0.1rem 0.45rem;
  border-radius:4px; }
pre { background: var(--bg); padding: 0.6rem 0.8rem; border-radius: 8px; overflow-x: auto;
  font-size: 0.78rem; line-height: 1.45; white-space: pre-wrap; word-break: break-word; }
details { margin-top: 0.4rem; }
summary { cursor: pointer; color: var(--text-secondary); font-size: 0.8rem; }
.fact-bad { color: var(--red); }
.fact-ok { color: var(--green); }
"""

# Длиннее этого результат инструмента показывается обрезанным: полный текст в JSON.
PREVIEW_CHARS = 4000


def render_trace(trace: Trace) -> str:
    totals = trace.totals()
    body = [_summary(trace, totals), _facts_section(trace)]
    body.extend(_step(step) for step in trace.steps)

    doc = page(
        title="Трасса разведки",
        subtitle=f"{trace.repo} · {trace.version_label} · коммит {trace.commit[:8]}",
        badge=(
            f"{esc(trace.model)} · промпт v{esc(trace.prompt_version)} "
            f"({esc(trace.prompt_sha)}) · шагов {totals['steps']}"
        ),
        body="\n".join(body),
        footer="Полный текст прочитанных файлов сохранён в JSON-версии трассы",
    )
    return doc.replace("</style>", _CSS + "</style>")


def _summary(trace: Trace, totals: dict) -> str:
    rows = [
        ("Endpoint", trace.endpoint),
        ("Модель", trace.model),
        ("Промпт", f"v{trace.prompt_version} · {trace.prompt_sha}"),
        ("Паки промпта", ", ".join(trace.packs) or "только ядро"),
        ("Не разобрано статически",
         f"{trace.stack.get('unparsed_files', 0)} файл(ов) "
         f"{', '.join(trace.stack.get('unparsed_languages', []))}".strip()),
        ("Причина остановки", trace.stop_reason),
        ("Обход полный", "нет" if trace.incomplete else "да"),
        ("Шагов", totals["steps"]),
        ("Вызовов инструментов", totals["tool_calls"]),
        ("Ошибок разбора ответа", totals["parse_errors"]),
        ("Фактов принято / отброшено", f"{totals['accepted']} / {totals['rejected']}"),
        ("Прочитано файлов / байт", f"{totals['files_read']} / {totals['bytes_read']}"),
        ("Токенов запрос / ответ",
         f"{totals['prompt_tokens']} / {totals['completion_tokens']}"),
        ("Секунд в модели", totals["seconds"]),
    ]
    cells = "".join(
        f"<tr><th>{esc(name)}</th><td>{esc(value)}</td></tr>" for name, value in rows
    )
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--accent-light);color:var(--accent);">Σ</div>
      <h2>Сводка прогона</h2>
    </div>
    <div class="section-body">
      <table class="obj-table">{cells}</table>
    </div>
  </div>
"""


def _label(item: dict) -> str:
    """Как назвать наблюдение в трассе.

    У универсальных находок `kind` пуст, и строка выглядела бы как «✓ путь:
    строка — claim» без типа — именно на тех стеках, ради которых
    универсализация и делалась. Показываем фасеты, как это делает ответ
    `note_fact`.
    """
    kind = str(item.get("kind") or "").strip()
    if kind:
        return kind
    category = item.get("category") or "?"
    action = item.get("action") or "—"
    mechanism = item.get("mechanism") or "—"
    return f"{category}/{action}/{mechanism}"


def _facts_section(trace: Trace) -> str:
    if not trace.accepted_facts and not trace.rejected_facts:
        return ""
    items = []
    for fact in trace.rejected_facts:
        items.append(
            f'<div class="fact-bad">✗ {esc(_label(fact))} '
            f'{esc(fact.get("path"))}:{esc(fact.get("line"))} — {esc(fact.get("reason"))}</div>'
        )
    for fact in trace.accepted_facts:
        items.append(
            f'<div class="fact-ok">✓ {esc(_label(fact))} '
            f'{esc(fact.get("path"))}:{esc(fact.get("line"))} — {esc(fact.get("claim"))}</div>'
        )
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:var(--amber-light);color:var(--amber);">!</div>
      <h2>Факты: сначала отброшенные</h2>
      <span class="count">{len(trace.rejected_facts)} / {len(trace.accepted_facts)}</span>
    </div>
    <div class="section-body">{''.join(items)}</div>
  </div>
"""


def _step(step) -> str:
    bad = not step.ok or step.action in {"parse_error", "error"}
    cls, label = _badge(step)

    parts = []
    if step.args:
        parts.append(f"<div>аргументы: <code>{esc(step.args)}</code></div>")
    if step.note:
        parts.append(f'<div class="fact-bad">{esc(step.note)}</div>')
    for verdict in step.verdicts:
        mark = "✓" if verdict.get("ok") else "✗"
        # Отдельное имя: `cls` выше — класс бейджа шага, и переиспользование
        # затирало его, из-за чего именно на шагах с фактами шапка теряла цвет.
        verdict_cls = "fact-ok" if verdict.get("ok") else "fact-bad"
        parts.append(
            f'<div class="{verdict_cls}">{mark} {esc(_label(verdict))} '
            f'{esc(verdict.get("path"))}:{esc(verdict.get("line"))} — '
            f'{esc(verdict.get("reason"))}</div>'
        )
    if step.result:
        parts.append(_collapsible("результат инструмента", step.result))
    if step.raw_response:
        parts.append(_collapsible("ответ модели", step.raw_response))
    if step.request:
        # Что именно ушло в модель: без этого не отличить ошибку рассуждения
        # от того, что нужный кусок просто не доехал в окне истории.
        sent = "\n\n".join(
            f"[{m.get('role', '')}] {m.get('chars', 0)} симв.\n{m.get('preview', '')}…"
            for m in step.request
        )
        parts.append(_collapsible(f"запрос к модели ({len(step.request)} сообщ.)", sent))

    return f"""
  <div class="step{' step-bad' if bad else ''}">
    <div class="step-head">
      <span class="step-num">#{step.number}</span>
      <span class="chip {cls}">{esc(label)}</span>
      <span style="color:var(--text-secondary);">{step.seconds:.1f} с</span>
      {'<span style="color:var(--text-secondary);">из кеша</span>' if step.from_cache else ''}
    </div>
    <div class="step-body">{''.join(parts)}</div>
  </div>
"""


def _badge(step) -> tuple[str, str]:
    if step.action == "final":
        return "badge-final", "финал"
    if step.action in {"parse_error", "error"}:
        return "badge-err", "ошибка"
    return ("badge-tool", step.tool or "инструмент") if step.ok else ("badge-err", step.tool)


def _collapsible(title: str, text: str) -> str:
    shown = text[:PREVIEW_CHARS]
    tail = f"\n… обрезано, всего {len(text)} символов" if len(text) > PREVIEW_CHARS else ""
    return (
        f"<details><summary>{esc(title)} ({len(text)} симв.)</summary>"
        f"<pre>{esc(shown + tail)}</pre></details>"
    )
