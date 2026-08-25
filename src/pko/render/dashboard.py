"""Единая точка входа отчёта: `index.html`.

Раньше результат состоял из четырёх файлов — таксономия, паспорта, карточка
допуска, сравнение, — и читателю приходилось самому решать, с какого начать.
Ни один из них не отвечал на первый вопрос владельца процесса: «можно это
запускать или нет, и если нет — что мешает».

Страница отвечает по порядку:

  1. Что это за система и каков вердикт.
  2. Что мешает допуску — список задач, а не проценты.
  3. Готовность к промышленному контуру §6 — отдельно от допуска.
  4. Как система работает: блоки, операции, ограничения.
  5. Чего PKO не проверял — границы анализа названы прямо.

Всё остальное — паспорта, доказательства, факты — открывается по клику из тех
же данных, что уже собраны для картотеки, и лежит рядом отдельными JSON.
Страница автономна: ни одного внешнего ресурса, открывается из файла.
"""

from __future__ import annotations

from typing import Any

from pko.gate.decide import ALLOW, ALLOW_WITH_RESTRICTIONS, DECISION_DENY, NO_DECISION
from pko.gate.record import UNSET
from pko.model.readiness import MISSING, NEEDS_RUNTIME, PARTIAL, READY, Readiness
from pko.model.schema import PkoModel
from pko.render.base import authorship, esc, meta_bar, page, split_gaps

_DASHBOARD_CSS = """
.verdict {
  display: flex; align-items: center; gap: 1rem; padding: 1.25rem 1.5rem;
  border-radius: 12px; margin-bottom: 1.5rem; border: 1px solid var(--border);
}
.verdict .mark { font-size: 1.6rem; font-weight: 800; letter-spacing: -0.02em; }
.verdict .why { font-size: 0.9rem; line-height: 1.55; }
.v-allow { background: var(--green-light); border-color: var(--green); }
.v-allow .mark { color: var(--green); }
.v-restrict { background: var(--amber-light); border-color: var(--amber); }
.v-restrict .mark { color: var(--amber); }
.v-deny { background: var(--red-light); border-color: var(--red); }
.v-deny .mark { color: var(--red); }
.v-none { background: var(--bg); }
.v-none .mark { color: var(--text-secondary); }
.todo { display: flex; flex-direction: column; gap: 0.6rem; }
.todo-item {
  display: flex; gap: 0.75rem; padding: 0.75rem 1rem; background: var(--bg);
  border-radius: 8px; font-size: 0.9rem;
}
.todo-item .what { flex: 1; }
.todo-item .why { color: var(--text-secondary); font-size: 0.84rem; margin-top: 0.2rem; }
.area-row {
  display: flex; align-items: baseline; gap: 0.75rem; padding: 0.6rem 0;
  border-bottom: 1px solid var(--border); font-size: 0.88rem;
}
.area-row:last-child { border-bottom: none; }
.area-row .aname { width: 200px; font-weight: 600; flex-shrink: 0; }
.area-row .abasis { color: var(--text-secondary); flex: 1; }
.state {
  display: inline-block; font-size: 0.7rem; font-weight: 700; padding: 0.1rem 0.45rem;
  border-radius: 4px; white-space: nowrap;
}
.s-ready { background: var(--green-light); color: var(--green); }
.s-partial { background: var(--amber-light); color: var(--amber); }
.s-missing { background: var(--red-light); color: var(--red); }
.s-runtime { background: var(--purple-light); color: var(--purple); }
.counters { display: flex; flex-wrap: wrap; gap: 0.75rem; }
.counter {
  background: var(--bg); border-radius: 10px; padding: 0.75rem 1.1rem; min-width: 110px;
}
.counter .num { font-size: 1.35rem; font-weight: 700; }
.counter .lbl { font-size: 0.78rem; color: var(--text-secondary); }
details.block { margin-top: 0.75rem; }
details.block > summary {
  cursor: pointer; font-size: 0.88rem; color: var(--accent); padding: 0.3rem 0;
}
"""

_STATE_CLASS = {
    READY: "s-ready", PARTIAL: "s-partial", MISSING: "s-missing",
    NEEDS_RUNTIME: "s-runtime",
}
_STATE_LABEL = {
    READY: "подтверждено", PARTIAL: "частично", MISSING: "не выполнено",
    NEEDS_RUNTIME: "нужен исполняющий контур",
}

_VERDICT_CLASS = {
    ALLOW: "v-allow", ALLOW_WITH_RESTRICTIONS: "v-restrict",
    DECISION_DENY: "v-deny", NO_DECISION: "v-none",
}
_VERDICT_TEXT = {
    ALLOW: "Запуск разрешён",
    ALLOW_WITH_RESTRICTIONS: "Запуск разрешён с ограничениями",
    DECISION_DENY: "Запуск не разрешён",
    NO_DECISION: "Решение не выносилось",
    "REQUIRE_FULL_CONTOUR": "Нужен полный контур требований",
}


def render_dashboard(
    model: PkoModel,
    checks: list[Any],
    decision: Any,
    readiness: Readiness,
    overview: str = "",
    links: dict[str, str] | None = None,
    record: Any = None,
    overview_source: str = "",
) -> str:
    """Собрать `index.html` — единственную страницу, с которой начинают чтение."""
    links = links or {}
    body = [
        meta_bar(model),
        _verdict_section(decision),
        _scope_section(record),
        _overview_section(overview, model, overview_source),
        _todo_section(checks, decision),
        _readiness_section(readiness),
        _shape_section(model),
        _limits_section(model),
        _files_section(links),
    ]
    meta = model.meta
    doc = page(
        title="Паспорт автономного процесса",
        subtitle=f"{meta.get('repo', '')} · восстановлено по коду",
        badge=f"Стандарт автономного процесса v1.1 · версия "
              f"{esc(meta.get('version_label', ''))}",
        body="\n".join(part for part in body if part),
        footer="Решение вычислено детерминированно; языковая модель в нём не участвует. "
               "Значения без пометки найдены в коде.",
    )
    return doc.replace("</style>", _DASHBOARD_CSS + "</style>")


def _verdict_section(decision: Any) -> str:
    """Первое, что видит читатель: можно запускать или нет."""
    value = getattr(decision, "decision", NO_DECISION)
    css = _VERDICT_CLASS.get(value, "v-none")
    text = _VERDICT_TEXT.get(value, value)
    mode = getattr(decision, "max_allowed_mode", None)
    requested = getattr(decision, "requested_mode", "")
    reasons = getattr(decision, "reasons", []) or []

    detail = f"Запрошенный режим: {esc(requested)}."
    if mode:
        detail += f" Максимально разрешённый: {esc(mode)}."
    else:
        detail += " Допуск не выдан, поэтому разрешённого режима нет."
    if reasons:
        detail += " " + esc(reasons[0])

    return f"""
  <div class="verdict {css}">
    <div class="mark">{esc(text)}</div>
    <div class="why">{detail}</div>
  </div>
"""


def _scope_section(record: Any) -> str:
    """Граница решения и срок его действия без расширяющих умолчаний.

    Вердикт без границ читается шире, чем он есть: «запуск разрешён» без
    названного scope выглядит разрешением на всё, что делает система.
    """
    if record is None:
        return ""
    scope, validity = record.scope, record.validity
    # Пояснение под строкой объясняет **последствие**, а не повторяет значение.
    # Для полей владельца оно появляется только когда поле пустое: под
    # заполненной границей приписка «владелец не задал границу» противоречит
    # тому, что читатель видит строкой выше.
    rows = [
        ("Граница решения", record.decision_boundary,
         _if_empty(record.decision_boundary,
                   "граница не определена — это не разрешение ни на процесс, ни на компонент")),
        ("Что входит в допуск", _joined(scope.in_scope),
         _if_empty(scope.in_scope,
                   "разрешённый scope не задан — ни одна операция не считается разрешённой")),
        ("Что исключено", _joined(scope.out_of_scope),
         _if_empty(scope.out_of_scope,
                   "отсутствие списка не расширяет in-scope: всё остальное остаётся вне допуска")),
        ("Запрещённые эффекты", _joined(scope.forbidden_effects),
         _if_empty(scope.forbidden_effects,
                   "политика не задана — до её явного подтверждения полномочия не выдаются")),
        ("Среда и когорта", _pair(scope.environment, scope.cohort), ""),
        ("Действует для", validity.bound_to,
         "любое изменение кода или намерения требует нового прогона"),
        ("Периметр анализа", scope.analysed_perimeter,
         "это граница разбора, а не разрешённый scope"),
    ]
    inner = "".join(
        f'<div class="area-row"><span class="aname">{esc(name)}</span>'
        f'<span class="abasis">{esc(value)}'
        + (f'<div class="why">{esc(note)}</div>' if note else "")
        + "</span></div>"
        for name, value, note in rows
    )
    decision = record.decision.get("decision", NO_DECISION)
    title = ("На что выдан допуск"
             if decision in {ALLOW, ALLOW_WITH_RESTRICTIONS}
             else "Граница решения — допуск не выдан")
    return _section("§", "var(--bg)", "var(--text-secondary)", title, inner)


def _joined(values: list[str]) -> str:
    return "; ".join(values) if values else UNSET


def _if_empty(value: Any, note: str) -> str:
    """Пояснение только для незаполненного поля."""
    return note if not value or value == UNSET else ""


def _pair(environment: str, cohort: str) -> str:
    return f"{environment} · {cohort}"


def _overview_section(overview: str, model: PkoModel, source: str = "") -> str:
    """Обзор с указанием авторства.

    Единственный кусок отчёта, который может быть написан языковой моделью, —
    этот. Без пометки читатель приписывает его тому же детерминированному
    разбору, что и всё остальное, и объяснение начинает весить как факт.
    """
    text = overview or _deterministic_overview(model)
    origin = authorship(source if overview else "")
    body = (f'<div style="font-size:0.95rem;line-height:1.7;">{esc(text)}</div>'
            f'<div class="authorship">{esc(origin)}</div>')
    return _section("≡", "var(--accent-light)", "var(--accent)", "Что это за система", body)


def _deterministic_overview(model: PkoModel) -> str:
    counts = model.counts()
    return (
        f"Репозиторий {model.meta.get('repo', '')} на коммите "
        f"{str(model.meta.get('commit', ''))[:8]}: восстановлено блоков "
        f"{counts.get('BBB', 0)}, атомарных операций {counts.get('AO', 0)}, "
        f"ограничений {counts.get('GUARDRAIL', 0)}. Проанализировано "
        f"{model.coverage.ratio:.0%} файлов backend-периметра."
    )


def _todo_section(checks: list[Any], decision: Any) -> str:
    """Что мешает допуску — задачами, а не процентами."""
    failed = [c for c in checks if getattr(c, "status", "") == "FAIL"]
    no_decision = getattr(decision, "decision", "") == NO_DECISION
    decision_reasons = getattr(decision, "reasons", []) or []
    if not failed and not no_decision:
        return _section("✓", "var(--green-light)", "var(--green)", "Что мешает запуску",
                        '<div class="desc-cell">Ни одна применимая проверка не провалена.</div>')

    blocking = set(getattr(decision, "blocking", []) or [])
    items = []
    if no_decision:
        reason = decision_reasons[0] if decision_reasons else (
            "Не подтверждены обязательные поля бизнес-намерения и границы полномочий"
        )
        items.append(
            '<div class="todo-item"><span class="state s-missing">нет решения</span>'
            f'<div class="what">Подтвердить бизнес-намерение и границу полномочий'
            f'<div class="why">{esc(reason)}</div></div></div>'
        )
    for check in sorted(failed, key=lambda c: (c.id not in blocking, c.id)):
        weight = "блокирует допуск" if check.id in blocking else "ограничивает режим или scope"
        items.append(
            f'<div class="todo-item"><span class="state s-missing">{esc(weight)}</span>'
            f'<div class="what">{esc(check.claim)}'
            f'<div class="why">{esc(check.basis)}</div></div></div>'
        )
    return _section("!", "var(--red-light)", "var(--red)", "Что мешает запуску",
                    f'<div class="todo">{"".join(items)}</div>', count=len(items))


def _readiness_section(readiness: Readiness) -> str:
    """Готовность к §6 — отдельно от допуска и без процентов."""
    rows = []
    for area in readiness.areas:
        css = _STATE_CLASS.get(area.state, "s-partial")
        label = _STATE_LABEL.get(area.state, area.state)
        action = (f'<div class="why">{esc(area.next_action)}</div>'
                  if area.next_action else "")
        rows.append(
            f'<div class="area-row"><span class="aname">{esc(area.name)}</span>'
            f'<span class="state {css}">{esc(label)}</span>'
            f'<span class="abasis">{esc(area.basis)}{action}</span></div>'
        )
    inner = (f'<div style="font-size:0.9rem;margin-bottom:0.9rem;">'
             f'{esc(readiness.summary)}</div>{"".join(rows)}')
    return _section("6", "var(--purple-light)", "var(--purple)",
                    "Готовность к промышленному контуру", inner)


def _shape_section(model: PkoModel) -> str:
    """Как система устроена: счётчики и разворачиваемый состав."""
    counts = model.counts()
    named = (("BBB", "переиспользуемых блоков"), ("AO", "атомарных операций"),
             ("GUARDRAIL", "ограничений"))
    counters = "".join(
        f'<div class="counter"><div class="num">{counts.get(kind, 0)}</div>'
        f'<div class="lbl">{esc(label)}</div></div>'
        for kind, label in named
    )
    details = []
    for kind, label in named:
        objects = model.by_kind(kind)
        if not objects:
            continue
        rows = "".join(
            f"<div class='area-row'><span class='aname'>{esc(o.id)}</span>"
            f"<span class='abasis'>{esc(o.name)}</span></div>"
            for o in objects
        )
        details.append(
            f'<details class="block"><summary>{esc(label.capitalize())}: '
            f'{len(objects)}</summary>{rows}</details>'
        )
    return _section("4", "var(--amber-light)", "var(--amber)", "Как система работает",
                    f'<div class="counters">{counters}</div>{"".join(details)}')


def _limits_section(model: PkoModel) -> str:
    """Чего PKO не проверял. Раздел обязателен даже когда пробелов нет."""
    gaps, run_notes = split_gaps(model.gaps)
    if not gaps and not run_notes:
        return ""
    items = "".join(f'<div class="gap-item">{esc(g)}</div>' for g in gaps)
    notes = ""
    if run_notes:
        notes = ('<div class="run-notes">О прогоне: '
                 + " · ".join(esc(n) for n in run_notes) + "</div>")
    return _section("?", "var(--amber-light)", "var(--amber)", "Чего анализ не покрыл",
                    f'<div class="gap-list">{items}</div>{notes}', count=len(gaps))


def _files_section(links: dict[str, str]) -> str:
    if not links:
        return ""
    rows = "".join(
        f'<div class="area-row"><span class="aname">'
        f'<a href="{esc(name)}" style="color:var(--accent);">{esc(name)}</a></span>'
        f'<span class="abasis">{esc(what)}</span></div>'
        for name, what in links.items()
    )
    return _section("→", "var(--bg)", "var(--text-secondary)", "Подробности и аудиты", rows)


def _section(icon: str, bg: str, fg: str, title: str, inner: str,
             count: int | None = None) -> str:
    badge = f'<span class="count">{count}</span>' if count is not None else ""
    return f"""
  <div class="section">
    <div class="section-header">
      <div class="icon" style="background:{bg};color:{fg};">{esc(icon)}</div>
      <h2>{esc(title)}</h2>{badge}
    </div>
    <div class="section-body">{inner}</div>
  </div>
"""
