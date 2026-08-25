"""Реестр структурных проверок по механизмам.

Раньше признаки лежали одним словарём в `verify.py` и покрывали ровно четыре
технологии: SQL, LangGraph, FastAPI. Механизм, которого там не было, нельзя
было ни подтвердить, ни честно отклонить — агент либо молчал, либо подгонял
находку под чужой вид.

Здесь у каждого семейства механизмов свой модуль, а реестр отвечает на два
вопроса:

  * умеем ли мы вообще проверять этот механизм (`is_covered`);
  * видна ли по указанной ссылке соответствующая конструкция (`mismatch`).

Разница между «не подтвердилось» и «не умеем проверять» существенна: первое
отбрасывает находку, второе оставляет её в отчёте, но лишает права влиять на
вердикт Gate. Так новая технология расширяет картину системы, но не может
через агента выдать допуск.
"""

from __future__ import annotations

import re

from pko.agent.verifiers import controls, data, flow, interfaces, messaging
from pko.model.taxonomy import Facets

# Ключ — `(механизм, действие)`; пустое действие означает «любое».
_REGISTRY: dict[tuple[str, str], re.Pattern[str]] = {}
for module in (data, flow, interfaces, messaging, controls):
    _REGISTRY.update(module.PATTERNS)

# Окно поиска признака: конструкция может занимать несколько строк.
KIND_WINDOW = 8

# Комментарий — не код. Слово в нём не доказывает ничего, а с признаком в виде
# вызова совпадает легко: «# здесь мог бы быть add_node».
_LINE_COMMENT = re.compile(r"(?m)(#|//).*$")

# Эти механизмы способны участвовать в политиках Gate после структурной
# проверки. SQL намеренно отсутствует: запрос действительно живёт в строковом
# литерале, а регулярное выражение не отличит выполняемый запрос от примера в
# docstring. SQL Gate по-прежнему питается детерминированным AST-экстрактором.
_GATE_SAFE_MECHANISMS = frozenset({
    "http_server", "ui_event", "cli", "cron", "queue", "webhook",
    "graph", "agent_tool", "orm", "limit",
})


def pattern_for(facets: Facets) -> re.Pattern[str] | None:
    """Шаблон признака для механизма и действия.

    Сначала точное совпадение по действию, затем общий шаблон механизма
    `(механизм, "")` — и всё. Объединять шаблоны разных действий нельзя:
    у направленных механизмов конструкции противоположны по смыслу, и
    объединение делало бы отправку в очередь доказательством обработчика,
    а ребро графа — доказательством узла. Нет подходящего шаблона — механизм
    в этом сочетании считается непроверяемым: наблюдение остаётся в отчёте,
    но вердикта не касается.
    """
    exact = _REGISTRY.get((facets.mechanism, facets.action))
    if exact is not None:
        return exact
    return _REGISTRY.get((facets.mechanism, ""))


def is_covered(facets: Facets) -> bool:
    """Умеет ли PKO подтверждать это сочетание структурно."""
    return pattern_for(facets) is not None


def is_gate_eligible(facets: Facets) -> bool:
    """Достаточна ли эта структурная проверка именно для решения Gate."""
    return facets.mechanism in _GATE_SAFE_MECHANISMS and is_covered(facets)


def mismatch(facets: Facets, lines: list[str], line: int) -> str:
    """Причина отказа, если признака механизма в окрестности строки нет."""
    pattern = pattern_for(facets)
    if pattern is None:
        return ""
    low = max(1, line - KIND_WINDOW)
    high = min(len(lines), line + KIND_WINDOW)
    window = _LINE_COMMENT.sub("", "\n".join(lines[low - 1: high]))
    # SQL находится внутри строки по определению; его можно показать в отчёте,
    # но нельзя повышать до Gate evidence этим regex. Для остальных механизмов
    # строковые литералы и docstring маскируются, чтобы `"app.route(...)"` и
    # `"graph.add_node(...)"` не выглядели исполняемыми конструкциями.
    if facets.mechanism != "sql":
        window = _without_literals(window)
    if pattern.search(window):
        return ""
    what = f"{facets.category}/{facets.action or '—'}/{facets.mechanism}"
    return (
        f"наблюдение {what} влияет на вердикт Gate, но в {line}±{KIND_WINDOW} нет "
        f"соответствующей конструкции — принято только утверждение агента"
    )


def _without_literals(text: str) -> str:
    """Замаскировать quoted literals, сохранив строки и позиции внешнего кода.

    Это небольшой консервативный lexer для Python/JS/TS: одинарные, двойные,
    тройные строки и template literals. При незакрытом литерале остаток окна
    тоже маскируется — лучше не подтвердить находку, чем выдать Gate evidence
    по тексту документации.
    """
    out = list(text)
    i = 0
    quote = ""
    triple = False
    escaped = False
    while i < len(text):
        ch = text[i]
        if quote:
            if ch != "\n":
                out[i] = " "
            if escaped:
                escaped = False
                i += 1
                continue
            if ch == "\\":
                escaped = True
                i += 1
                continue
            if triple and text.startswith(quote * 3, i):
                for j in range(i, min(i + 3, len(out))):
                    out[j] = " "
                i += 3
                quote = ""
                triple = False
                continue
            if not triple and ch == quote:
                quote = ""
            i += 1
            continue
        if ch in {"'", '"'}:
            triple = text.startswith(ch * 3, i)
            quote = ch
            width = 3 if triple else 1
            for j in range(i, min(i + width, len(out))):
                out[j] = " "
            i += width
            continue
        if ch == "`":
            quote = ch
            out[i] = " "
        i += 1
    return "".join(out)


def patterns_by_mechanism() -> dict[str, list[re.Pattern[str]]]:
    """Признаки, сгруппированные по механизму.

    Тем же реестром пользуется определение стека: отдельная таблица шаблонов
    разошлась бы с проверкой, и пак подключался бы там, где подтвердить
    находку всё равно нечем.
    """
    out: dict[str, list[re.Pattern[str]]] = {}
    for (mechanism, _action), pattern in _REGISTRY.items():
        out.setdefault(mechanism, []).append(pattern)
    return out


def covered_mechanisms() -> list[str]:
    """Список механизмов с проверкой — идёт в промпт и в трассу."""
    return sorted({mechanism for mechanism, _ in _REGISTRY})
