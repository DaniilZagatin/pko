"""Проверка находок агента по коду.

Прямой аналог `pko.report.guard`, но для фактов, а не для прозы. Правило одно:
утверждение живёт, только если его видно по указанной ссылке на том же коммите.
Файл и строка должны существовать, а якорь из `claim` — встречаться рядом.

Смысл ограничения: `OBSERVED` обязан означать «код это показывает», а не «модель
так сказала». Не подтвердилось — факт отбрасывается с причиной, и причина
попадает в трассу рядом с породившим шагом.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pko.agent import verifiers
from pko.extractors.base import FACT_KINDS, Fact, Tree
from pko.model import taxonomy

# Насколько далеко от указанной строки ищем якорь: агент часто указывает начало
# конструкции, а имя встречается строкой ниже.
ANCHOR_WINDOW = 5

# Слова короче этого не являются якорем: «to», «is», «db» совпадут где угодно.
MIN_ANCHOR = 4

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]{3,}")


@dataclass
class Verdict:
    ok: bool
    fact: Fact | None
    reason: str
    proposal: dict[str, Any]


def verify_facts(proposals: list[dict[str, Any]], tree: Tree) -> tuple[list[Fact], list[Verdict]]:
    """Вернуть подтверждённые факты и вердикты по каждому предложению."""
    known = set(tree.files)
    accepted: list[Fact] = []
    verdicts: list[Verdict] = []

    for proposal in proposals:
        verdict = _verify_one(proposal, tree, known)
        verdicts.append(verdict)
        if verdict.ok and verdict.fact is not None:
            accepted.append(verdict.fact)
    return accepted, verdicts


def _facets_of(
    proposal: dict[str, Any], kind: str
) -> tuple[taxonomy.Facets, taxonomy.Facets, str]:
    """Фасеты предложения и причина, если они противоречивы.

    Агент вправе описать находку и универсально (`category`/`action`/
    `mechanism`), и старым видом. Второе оставлено намеренно: на знакомом
    стеке короткое `SQL_WRITE` точнее и дешевле, чем три поля.

    Признаки проверяются не по отдельности, а как целое. Иначе механизм
    подтверждался конструкцией, а категория принималась на слово: настоящая
    запись, объявленная точкой входа, проходила проверку, исчезала из эффектов
    и попадала в доказательства траектории. Ошибка классификации меняла вход
    детерминированного Gate — ровно то, что верификация обязана исключать.
    """
    base = taxonomy.facets_for(kind)
    explicit = taxonomy.Facets(
        category=taxonomy.normalize_category(proposal.get("category", "")),
        action=taxonomy.normalize_action(proposal.get("action", "")),
        mechanism=taxonomy.normalize_mechanism(proposal.get("mechanism", "")),
    )
    facets = taxonomy.Facets(
        category=explicit.category or base.category,
        action=explicit.action or base.action,
        mechanism=explicit.mechanism or base.mechanism,
    )
    return facets, explicit, taxonomy.legacy_conflict(kind, explicit) or taxonomy.conflict(facets)


def _verify_one(proposal: dict[str, Any], tree: Tree, known: set[str]) -> Verdict:
    kind = str(proposal.get("kind", "")).strip()
    claim = str(proposal.get("claim", "")).strip()
    path = str(proposal.get("path", "")).strip()
    raw_line = proposal.get("line")

    facets, _explicit, disagreement = _facets_of(proposal, kind)
    if kind and kind not in FACT_KINDS:
        return Verdict(False, None, f"неизвестный kind «{kind}»", proposal)

    # Признаки на входе проверяются тем же правилом, что и в `note_fact`:
    # иначе инструмент отвечал бы «принято», а проверка отбрасывала молча.
    problem = taxonomy.proposal_problem(
        kind, str(proposal.get("category", "")), str(proposal.get("action", "")),
        str(proposal.get("mechanism", "")),
    )
    if problem:
        return Verdict(False, None, problem, proposal)
    if disagreement:
        return Verdict(False, None, f"признаки наблюдения противоречивы: {disagreement}", proposal)
    if not claim:
        return Verdict(False, None, "пустой claim", proposal)
    if path not in known:
        return Verdict(False, None, f"пути {path!r} нет на этом коммите", proposal)

    text = tree.read(path)
    if text is None:
        return Verdict(False, None, f"файл {path} не читается", proposal)

    lines = text.splitlines()
    try:
        line = int(raw_line) if raw_line is not None else 0
    except (TypeError, ValueError):
        return Verdict(False, None, f"строка {raw_line!r} не является числом", proposal)
    if line < 1 or line > len(lines):
        return Verdict(
            False, None,
            f"строка {line} вне файла {path} (всего {len(lines)})", proposal,
        )

    anchor = _find_anchor(claim, lines, line)
    if anchor is None:
        return Verdict(
            False, None,
            f"в {path}:{line}±{ANCHOR_WINDOW} нет ни одного слова из claim", proposal,
        )

    mismatch = verifiers.mismatch(facets, lines, line)
    if mismatch:
        return Verdict(False, None, mismatch, proposal)

    # Механизм без структурной проверки не отбрасывается: картина системы от
    # этого стала бы беднее без всякой пользы. Но и вердикт им подкрепить
    # нельзя — наблюдение идёт в паспорт и в пробелы, а не в решение.
    covered = verifiers.is_covered(facets)
    gate_eligible = verifiers.is_gate_eligible(facets)
    fact = Fact(
        kind=kind or facets.category,
        key=claim[:80],
        value=claim,
        path=path,
        line=line,
        basis=f"агент: {claim[:120]}",
        category=facets.category,
        action=facets.action,
        mechanism=facets.mechanism,
        gate_eligible=gate_eligible,
    )
    reason = f"подтверждено якорем «{anchor}»"
    if not covered:
        reason += (
            f"; механизм «{facets.mechanism or 'не указан'}» структурно не проверяется — "
            f"в вердикт Gate не входит"
        )
    elif not gate_eligible:
        reason += (
            "; конструкция распознана только эвристически и недостаточна для Gate — "
            "наблюдение остаётся в отчёте"
        )
    return Verdict(True, fact, reason, proposal)


# Структурные признаки переехали в `pko.agent.verifiers`: там они разложены по
# семействам механизмов и расширяются новой технологией без правки этого файла.
# Здесь остаётся переходник для прежних видов — им пользуются проверки, которые
# спрашивают про `SQL_WRITE` и `GRAPH_NODE` по имени.
KIND_WINDOW = verifiers.KIND_WINDOW


def _kind_mismatch(kind: str, lines: list[str], line: int) -> str:
    """Причина отказа, если для прежнего вида в коде нет признака."""
    return verifiers.mismatch(taxonomy.facets_for(kind), lines, line)


def _find_anchor(claim: str, lines: list[str], line: int) -> str | None:
    """Найти в окрестности строки слово, которое встречается и в claim."""
    tokens = {t for t in _TOKEN.findall(claim) if len(t) >= MIN_ANCHOR}
    # Числа и пути тоже годятся как якорь: `timeout = 60`, `/api/v1/tasks`.
    tokens.update(t for t in re.findall(r"[/\w.-]{4,}", claim) if any(c.isdigit() for c in t))
    if not tokens:
        return None

    low = max(1, line - ANCHOR_WINDOW)
    high = min(len(lines), line + ANCHOR_WINDOW)
    window = "\n".join(lines[low - 1: high]).lower()
    for token in sorted(tokens, key=len, reverse=True):
        if token.lower() in window:
            return token
    return None


def verify_invariants(
    proposals: list[dict[str, Any]], tree: Tree
) -> tuple[list[dict[str, Any]], list[str]]:
    """Инварианты guardrails: ссылок не верифицируем, но опора обязана существовать.

    Это интерпретация, а не факт, поэтому в модель поле уйдёт с `INFERRED`. Но
    предложение без единого реального пути не принимается вовсе: иначе в паспорт
    попадёт утверждение, к которому невозможно вернуться.
    """
    known = set(tree.files)
    accepted: list[dict[str, Any]] = []
    rejected: list[str] = []

    for proposal in proposals:
        text = str(proposal.get("invariant", "")).strip()
        path = str(proposal.get("path", "")).strip()
        if not text:
            rejected.append("инвариант без текста")
            continue
        if path not in known:
            rejected.append(f"инвариант «{text[:60]}»: опоры {path!r} нет на коммите")
            continue
        accepted.append({"invariant": text, "path": path, "line": proposal.get("line")})
    return accepted, rejected


def verify_groups(
    proposals: list[dict[str, Any]], tree: Tree
) -> tuple[dict[str, list[str]], list[str]]:
    """Группировка BBB: имя плюс реально существующие пути."""
    known = set(tree.files)
    groups: dict[str, list[str]] = {}
    rejected: list[str] = []

    for proposal in proposals:
        name = str(proposal.get("name", "")).strip()
        paths = [str(p).strip() for p in (proposal.get("paths") or []) if str(p).strip()]
        if not name:
            rejected.append("группа без названия")
            continue
        real = [p for p in paths if p in known or any(f.startswith(p.rstrip("/") + "/")
                                                      for f in known)]
        if not real:
            rejected.append(f"группа «{name}»: ни один из путей не существует")
            continue
        groups[name] = real
    return groups, rejected
