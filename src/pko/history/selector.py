"""Выбор версий репозитория для анализа.

Версия — это точка истории, для которой строится отдельная PKO-модель.
Базовый набор: первый коммит с прикладным кодом и текущий HEAD ветки. Если
запрошено больше версий, между ними равномерно добираются точки слияния
(merge-коммиты или коммиты с «Pull request #…» в теме — так Bitbucket помечает
squash-слияния, у которых нет второго родителя).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pko.errors import PkoError
from pko.git.repo import Commit, GitRepo

_PR_SUBJECT = re.compile(r"pull request\s*#?\d+", re.IGNORECASE)

DEFAULT_CODE_PATHSPECS = ["*.py"]


@dataclass(frozen=True)
class Version:
    """Одна анализируемая версия."""

    commit: Commit
    label: str
    reason: str

    @property
    def sha(self) -> str:
        return self.commit.sha


def is_integration_point(commit: Commit) -> bool:
    """Похож ли коммит на объединённую доработку."""
    return commit.is_merge or bool(_PR_SUBJECT.search(commit.subject))


def select_versions(
    repo: GitRepo,
    branch: str,
    max_versions: int = 2,
    code_pathspecs: list[str] | None = None,
) -> list[Version]:
    """Вернуть версии от старой к новой; в списке всегда есть HEAD."""
    if max_versions < 1:
        raise PkoError(
            "max_versions должен быть не меньше 1.",
            "Передайте --max-versions с положительным целым числом.",
        )

    head_sha = repo.resolve(branch)
    line = repo.first_parent(head_sha)
    if not line:
        raise PkoError(
            f"В ветке {branch} нет коммитов.",
            "Проверьте имя ветки и полноту локального зеркала.",
        )

    head = line[0]
    if max_versions == 1:
        return [Version(head, "current", "текущее состояние ветки")]

    # Поиск ограничен анализируемой веткой: иначе базовая версия пришла бы из
    # истории ветки по умолчанию и не имела отношения к сравнению.
    oldest_code = repo.first_commit_touching(
        code_pathspecs or DEFAULT_CODE_PATHSPECS, rev=head_sha
    )
    line_shas = {c.sha for c in line}
    # Первый коммит с кодом мог прийти из ветки и не лежать на первой линии —
    # тогда берём самый старый коммит основной линии, он точно сопоставим.
    if oldest_code is None or oldest_code.sha not in line_shas:
        oldest_code = line[-1]

    picked: list[Commit] = [oldest_code]

    slots = max_versions - 2
    if slots > 0:
        middle = [
            c
            for c in reversed(line)  # от старых к новым
            if is_integration_point(c) and c.sha not in {oldest_code.sha, head.sha}
        ]
        picked.extend(_evenly(middle, slots))

    if head.sha != oldest_code.sha:
        picked.append(head)

    return _label(_dedup(picked), head_sha=head.sha, oldest_sha=oldest_code.sha)


def _evenly(items: list[Commit], slots: int) -> list[Commit]:
    """Равномерная выборка без перекоса в начало или конец истории."""
    if not items or slots <= 0:
        return []
    if len(items) <= slots:
        return list(items)
    step = len(items) / (slots + 1)
    out: list[Commit] = []
    for i in range(1, slots + 1):
        idx = min(len(items) - 1, int(round(i * step)) - 1)
        if not out or out[-1].sha != items[idx].sha:
            out.append(items[idx])
    return out


def _dedup(commits: list[Commit]) -> list[Commit]:
    seen: set[str] = set()
    out: list[Commit] = []
    for c in commits:
        if c.sha not in seen:
            seen.add(c.sha)
            out.append(c)
    return out


def _label(commits: list[Commit], head_sha: str, oldest_sha: str) -> list[Version]:
    versions: list[Version] = []
    for i, c in enumerate(commits, start=1):
        if c.sha == head_sha:
            label, reason = "current", "текущее состояние ветки"
        elif c.sha == oldest_sha:
            label, reason = "v1", "первый коммит с прикладным кодом"
        else:
            label = f"v{i}"
            reason = (
                "объединённая доработка" if is_integration_point(c) else "точка истории"
            )
        versions.append(Version(commit=c, label=label, reason=reason))
    return versions
