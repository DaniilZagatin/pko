"""Проверка evidence, предложенной ролью matcher, по коду целевого репозитория.

Тот же принцип, что и в `pko.agent.verify` (`OBSERVED` означает «код это
показывает», а не «модель так сказала»): путь и строка обязаны существовать на
этом коммите, а рядом — находиться слово из основания. Но без привязки к
category/action/mechanism `pko.agent.verify.verify_facts` — пункт плана не
структурный факт кода с известным «механизмом», а утверждение более высокого
уровня («авторизация реализована»), которое может опираться на функцию, класс
или блок целиком. Поэтому здесь проверяется только путь/строка/якорь; заявленная
структурная привязка (§CHK-GRD-001 и подобные) к плану не относится.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pko.extractors.base import Tree

ANCHOR_WINDOW = 5
MIN_ANCHOR = 4

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]{3,}")


@dataclass(frozen=True)
class EvidenceVerdict:
    ok: bool
    path: str
    line: int | None
    basis: str
    reason: str


def verify_evidence(path: str, line: int | None, basis: str, tree: Tree) -> EvidenceVerdict:
    """Подтвердить одну evidence-ссылку, предложенную matcher'ом."""
    path = (path or "").strip()
    basis = (basis or "").strip()
    if not path:
        return EvidenceVerdict(False, path, line, basis, "пустой путь")
    if path not in set(tree.files):
        return EvidenceVerdict(False, path, line, basis, f"пути {path!r} нет на этом коммите")

    text = tree.read(path)
    if text is None:
        return EvidenceVerdict(False, path, line, basis, f"файл {path} не читается")

    lines = text.splitlines()
    if line is None:
        return EvidenceVerdict(True, path, None, basis, "путь существует, строка не указана")
    if line < 1 or line > len(lines):
        return EvidenceVerdict(
            False, path, line, basis, f"строка {line} вне файла {path} (всего {len(lines)})"
        )

    anchor = _find_anchor(basis, lines, line)
    if anchor is None:
        return EvidenceVerdict(
            False, path, line, basis,
            f"в {path}:{line}±{ANCHOR_WINDOW} нет ни одного слова из основания",
        )
    return EvidenceVerdict(True, path, line, basis, f"подтверждено якорем «{anchor}»")


def _find_anchor(basis: str, lines: list[str], line: int) -> str | None:
    tokens = {t for t in _TOKEN.findall(basis) if len(t) >= MIN_ANCHOR}
    tokens.update(t for t in re.findall(r"[/\w.-]{4,}", basis) if any(c.isdigit() for c in t))
    if not tokens:
        return None
    low = max(1, line - ANCHOR_WINDOW)
    high = min(len(lines), line + ANCHOR_WINDOW)
    window = "\n".join(lines[low - 1: high]).lower()
    for token in sorted(tokens, key=len, reverse=True):
        if token.lower() in window:
            return token
    return None
