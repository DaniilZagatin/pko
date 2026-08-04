"""BASIC Gate Card — одна компактная карточка по §5.1.1 и §8.0.1.

Отдельные паспорта, CHECK_RESULT, EVIDENCE_BUNDLE и прочие машинные ресурсы для
BASIC намеренно не создаются: стандарт считает это анти-паттерном (§Б.2).
Карточка ссылается на артефакты, а не копирует их содержимое.
"""

from __future__ import annotations

from typing import Any

from pko.gate.decide import NO_DECISION, GateDecision, summarize
from pko.gate.evaluate import CheckResult
from pko.model.schema import PkoModel

_STATUS_MARK = {
    "PASS": "`PASS`",
    "FAIL": "`FAIL`",
    "NOT_APPLICABLE": "`NOT_APPLICABLE`",
    "REQUIRES_FULL_CONTOUR": "`REQUIRES_FULL_CONTOUR`",
}


def render_gate_card(
    model: PkoModel,
    results: list[CheckResult],
    decision: GateDecision,
    intent: dict[str, Any] | None,
    generated_at: str,
) -> str:
    meta = model.meta
    intent = intent or {}
    profile = decision.profile
    counts = summarize(results)

    lines: list[str] = []
    add = lines.append

    add("# BASIC Gate Card")
    add("")
    add(f"> **Стандарт:** Автономный процесс v1.1 · машинный уровень `{profile.get('machine_level')}`  ")
    add(f"> **Сформировано:** {generated_at}  ")
    add(f"> **Версия карточки:** {meta.get('version_label', 'current')}-{str(meta.get('commit', ''))[:8]}")
    add("")
    add("---")
    add("")

    add("## 1. Идентификация и граница")
    add("")
    add("| Поле | Значение |")
    add("|---|---|")
    add(f"| Репозиторий | `{meta.get('repo', '—')}` |")
    add(f"| Ветка | `{meta.get('branch', '—')}` |")
    add(f"| Версия реализации | `{str(meta.get('commit', ''))[:12]}` от {meta.get('commit_date', '—')} |")
    add(f"| Граница решения | {_fmt(intent.get('decision_boundary', 'END_TO_END_PROCESS'))} |")
    add(f"| Назначение | {_fmt(intent.get('business_meaning')) or '**не подтверждено владельцем**'} |")
    add(f"| Пользователь результата | {_fmt(intent.get('client')) or '**не подтверждён**'} |")
    add(f"| Успешный исход | {_fmt(intent.get('target_state')) or '**не подтверждён**'} |")
    add(f"| Бизнес-владелец | {_fmt(intent.get('business_owner')) or '**не назначен**'} |")
    add(f"| In-scope | {_fmt(intent.get('in_scope')) or 'весь проанализированный код'} |")
    add(f"| Out-of-scope | {_fmt(intent.get('out_of_scope')) or 'непроанализированная часть репозитория'} |")
    add("")

    add("## 2. Профиль применения")
    add("")
    add("| Поле | Значение |")
    add("|---|---|")
    add(f"| Профиль | `{profile.get('profile')}` |")
    add(f"| Зона матрицы 0.2 | `{profile.get('zone')}` |")
    add(f"| Зрелость | {profile.get('inputs', {}).get('maturity', '—')} |")
    add(f"| Значимость последствий | {profile.get('inputs', {}).get('consequence', '—')} |")
    add(f"| Запрошенный режим | `{decision.requested_mode}` |")
    add(f"| Внешние эффекты | {_fmt(profile.get('inputs', {}).get('external_effects')) or 'не заявлены'} |")
    triggers = profile.get("triggers") or []
    add(f"| Триггеры FULL | {'; '.join(triggers) if triggers else 'нет'} |")
    add("")

    add("## 3. Проверки")
    add("")
    add(f"Пройдено `PASS`: {counts['PASS']} · не пройдено `FAIL`: {counts['FAIL']} · "
        f"неприменимо: {counts['NOT_APPLICABLE']}")
    add("")
    add("| Утверждение | Класс | Статус | Основание | Ссылка на факт |")
    add("|---|---|---|---|---|")
    for r in results:
        evidence = ", ".join(f"`{e}`" for e in r.evidence[:3]) if r.evidence else "—"
        add(
            f"| {_cell(r.claim)} | {r.requirement_class} | {_STATUS_MARK.get(r.status, r.status)} "
            f"| {_cell(r.basis)} | {evidence} |"
        )
    add("")

    add("## 4. Решение")
    add("")
    if decision.decision == NO_DECISION:
        add("> Решение о допуске **не выносится**: не подтверждено бизнес-намерение. "
            "Это черновик по коду, а не отказ в запуске — устанавливать режим исполнения "
            "и разрешённый scope не по чему, пока не заполнен `business_intent.yaml`.")
        add("")
    add("| Поле | Значение |")
    add("|---|---|")
    add(f"| Решение | **`{decision.decision}`** |")
    add(f"| Запрошенный режим | `{decision.requested_mode}` |")
    no_mode = "— (решение не выносится)" if decision.decision == NO_DECISION else "— (допуск не выдан)"
    add(f"| Максимально разрешённый режим | "
        f"{'`' + decision.max_allowed_mode + '`' if decision.max_allowed_mode else no_mode} |")
    blocking_label = (
        "Проверки, которые придётся закрыть" if decision.decision == NO_DECISION
        else "Блокирующие проверки"
    )
    add(f"| {blocking_label} | {', '.join(decision.blocking) if decision.blocking else 'нет'} |")
    add(f"| Ограничения | {'; '.join(decision.restrictions) if decision.restrictions else 'нет'} |")
    add(f"| Ссылка на реализацию | `{decision.implementation_ref}` |")
    add("")
    if decision.reasons:
        add("**Основания решения:**")
        add("")
        for reason in decision.reasons:
            add(f"- {reason}")
        add("")

    add("## 5. Что нужно закрыть")
    add("")
    todo = [r for r in results if r.status == "FAIL"]
    if not todo:
        add("Блокирующих замечаний нет.")
    else:
        for r in todo:
            add(f"- **{r.claim}** — {r.basis}")
    add("")

    if model.gaps:
        add("## 6. Пробелы анализа")
        add("")
        for gap in model.gaps:
            add(f"- {gap}")
        add("")

    add("---")
    add("")
    add(
        f"*Решение вычислено детерминированно по §5.2.3.4 стандарта. Языковая модель "
        f"в вычислении не участвует. Изменение коммита, конфигурации или набора "
        f"проверок требует новой версии карточки.*"
    )
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value in (None, "", []):
        return ""
    if isinstance(value, (list, tuple)):
        return _cell(", ".join(str(v) for v in value))
    return _cell(value)


def _cell(value: Any) -> str:
    """Экранировать разделитель столбцов.

    В ячейки попадает свободный текст владельца (назначение, границы, роль) и
    основания проверок. Одна вертикальная черта в таком тексте сдвигает всю строку
    таблицы, и карточка начинает читаться неверно.
    """
    return str(value).replace("|", "\\|")
