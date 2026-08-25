"""BASIC Gate Card — одна компактная карточка по §5.1.1 и §8.0.1.

Отдельные паспорта, CHECK_RESULT, EVIDENCE_BUNDLE и прочие машинные ресурсы для
BASIC намеренно не создаются: стандарт считает это анти-паттерном (§Б.2).
Карточка ссылается на артефакты, а не копирует их содержимое.
"""

from __future__ import annotations

from typing import Any

from pko.gate.decide import (
    ALLOW, ALLOW_WITH_RESTRICTIONS, NO_DECISION, GateDecision, summarize,
)
from pko.gate.evaluate import CheckResult
from pko.gate.record import UNSET, BasicRecord
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
    record: BasicRecord,
) -> str:
    """Карточка §5.1.1 — человекочитаемый вид записи §8.0.1, и только он.

    Запись обязательна, и это главное в подписи. Раньше её можно было не
    передавать: карточка собирала запись сама из `model`, `results`, `decision`
    и `intent`. Такой сборке неоткуда узнать файлы конфигурации (они в
    `extraction`) и незаполненные поля владельца (они в `IntentResult`), поэтому
    `pko gate` печатал «существенная конфигурация не найдена» на том же
    коммите, где `pko analyze` печатал `config/agent.json`, и молча терял раздел
    «Чего не хватает в самой записи». Собрать карточку в обход записи больше
    нельзя — расходиться нечему.

    Время формирования тоже берётся из записи (`validity.decided_at`): отдельный
    параметр позволял шапке и сроку действия показывать разные минуты.
    """
    meta = model.meta
    profile = decision.profile
    counts = summarize(results)
    generated_at = record.validity.decided_at

    lines: list[str] = []
    add = lines.append

    add("# BASIC Gate Card")
    add("")
    required = profile.get("required_machine_level", "BASIC_RECORD")
    achieved = profile.get("achieved_machine_level", "BASIC_RECORD")
    # Требуемый и достигнутый уровень печатаются раздельно: совпадение их
    # означало бы, что PKO выпустил ресурсную модель §8.1–8.14, а он выпускает
    # запись `BASIC_RECORD` и оценку готовности к остальному.
    level = (f"машинный уровень `{achieved}`" if required == achieved else
             f"требуется `{required}`, выпущено `{achieved}`")
    add(f"> **Стандарт:** Автономный процесс v1.1 · {level}  ")
    add(f"> **Сформировано:** {generated_at}  ")
    add(f"> **Версия карточки:** {record.record_version}")
    add("")
    add("---")
    add("")

    add("## 1. Идентификация и граница")
    add("")
    impl = record.implementation
    purpose = record.purpose_and_result
    add("| Поле | Значение |")
    add("|---|---|")
    add(f"| Репозиторий | `{impl.repo or '—'}` |")
    add(f"| Ветка | `{impl.branch or '—'}` |")
    add(f"| Версия реализации | `{impl.commit[:12]}` от {impl.commit_date or '—'} |")
    add(f"| Существенная конфигурация | {_config_cell(impl)} |")
    add(f"| Граница решения | {_owner_field(record.decision_boundary)} |")
    add(f"| Назначение | {_owner_field(purpose['purpose'])} |")
    add(f"| Пользователь результата | {_owner_field(purpose['client'])} |")
    add(f"| Успешный исход | {_owner_field(purpose['success_outcome'])} |")
    add(f"| Корректно остановленный исход | {_owner_field(purpose['stopped_outcome'])} |")
    add(f"| Бизнес-владелец | {_owner_field(record.business_owner)} |")
    add("")

    scope_heading = ("Разрешённый scope"
                     if decision.decision in {ALLOW, ALLOW_WITH_RESTRICTIONS}
                     else "Заявленный scope — допуск не выдан")
    add(f"## 2. {scope_heading}")
    add("")
    # Периметр анализа и разрешённый scope разведены намеренно: раньше в поле
    # In-scope печаталось «весь проанализированный код», и граница разбора
    # читалась как граница допуска.
    scope = record.scope
    add("| Поле | Значение |")
    add("|---|---|")
    add(f"| In-scope | {_fmt(scope.in_scope) or '**не задан — ничего не разрешено**'} |")
    add(f"| Out-of-scope | {_fmt(scope.out_of_scope) or '**не задан; это не расширяет in-scope**'} |")
    add(f"| Среда | {_owner_field(scope.environment)} |")
    add(f"| Когорта | {_owner_field(scope.cohort)} |")
    add(f"| Запрещённые эффекты | "
        f"{_fmt(scope.forbidden_effects) or '**политика не задана — полномочия не выдаются**'} |")
    add(f"| Периметр анализа | {_cell(scope.analysed_perimeter)} |")
    add("")
    add("*Периметр анализа — то, что разобрал PKO. Разрешённый scope задаёт владелец, "
        "и совпадать они не обязаны. Соблюдение scope при исполнении не проверяется: "
        "для этого нужна запись §8.0.2, которой PKO не производит.*")
    add("")

    add("## 3. Профиль применения")
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

    add("## 4. Проверки")
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

    add("## 5. Решение")
    add("")
    if decision.decision == NO_DECISION:
        add("> Решение о допуске **не выносится**: не подтверждено бизнес-намерение "
            "или явная граница полномочий. "
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
    add(f"| Ограничения | {'; '.join(record.restrictions) if record.restrictions else 'нет'} |")
    add(f"| Ссылка на реализацию | `{decision.implementation_ref}` |")
    add("")
    if decision.reasons:
        add("**Основания решения:**")
        add("")
        for reason in decision.reasons:
            add(f"- {reason}")
        add("")

    # Срок действия — обязательное поле §8.0.1. Решение без него читается как
    # бессрочное, хотя оно относится ровно к одному коммиту.
    validity = record.validity
    add("## 6. Срок действия и условия инвалидирования")
    add("")
    add("| Поле | Значение |")
    add("|---|---|")
    add(f"| Решение вынес | {_cell(validity.decided_by)} |")
    add(f"| Когда | {_cell(validity.decided_at)} |")
    add(f"| Действует для | {_cell(validity.bound_to)} |")
    add("")
    add("Запись перестаёт действовать, если:")
    add("")
    for condition in validity.invalidated_by:
        add(f"- {condition}")
    add("")

    add("## 7. Что нужно закрыть")
    add("")
    todo = [r for r in results if r.status == "FAIL"]
    if not todo and decision.decision == NO_DECISION:
        add("Формальные проверки не заменяют границу полномочий: решение не выносится, "
            "пока не заполнены обязательные поля `business_intent.yaml`.")
    elif not todo:
        add("Блокирующих замечаний нет.")
    else:
        for r in todo:
            add(f"- **{r.claim}** — {r.basis}")
    add("")

    if record.gaps:
        add("## 8. Чего не хватает в самой записи")
        add("")
        add("Поля §8.0.1, которые владелец не заполнил:")
        add("")
        for gap in record.gaps:
            add(f"- {gap}")
        add("")

    if model.gaps:
        add("## 9. Пробелы анализа")
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


def _owner_field(value: str) -> str:
    """Поле, которое заполняет владелец. Незаполненное выделяется, а не подменяется.

    Подставлять сюда догадку по коду нельзя: запись допуска фиксирует то, что
    подтвердил владелец, а не то, что удалось предположить.
    """
    return f"**{UNSET}**" if not value or value == UNSET else _cell(value)


def _config_cell(impl: Any) -> str:
    if not impl.config_files:
        return f"не найдена — {impl.config_note}"
    shown = ", ".join(f"`{p}`" for p in impl.config_files[:5])
    more = f" и ещё {len(impl.config_files) - 5}" if len(impl.config_files) > 5 else ""
    return _cell(shown + more)


def _fmt(value: Any) -> str:
    if value in (None, "", []):
        return ""
    if isinstance(value, (list, tuple)):
        return _cell(", ".join(str(v) for v in value))
    return _cell(value)


def _cell(value: Any) -> str:
    """Привести свободный текст к тому, что переживёт строку таблицы.

    В ячейки попадает текст владельца (назначение, границы, роль) и основания
    проверок. Ломают строку две вещи, и обе приходят из обычного заполнения
    `business_intent.yaml`:

    * вертикальная черта — сдвигает столбцы, и карточка читается неверно;
    * перевод строки — блочный скаляр `|` разрешён разбором намерения
      (`pko.util.yamlmini`), а физический перенос внутри ячейки обрывает
      таблицу: остаток текста становится обычным абзацем, а следующие поля
      записи допуска пропадают из таблицы вовсе.

    Переносы заменяются на `<br>`, а не на пробел: владелец разбил текст на
    строки намеренно, и в отрисованной карточке это разбиение сохраняется.
    """
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("|", "\\|").replace("\n", "<br>")
