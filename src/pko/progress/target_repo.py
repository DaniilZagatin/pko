"""Чтение целевого репозитория на зафиксированном коммите.

Переиспользует read-only git-обвязку и детерминированные экстракторы PKO без
изменений: те же гарантии («без мутирующих команд», факты со ссылкой
path:line) нужны и здесь — сопоставление плана с кодом строится на тех же
фактах, что и паспорта Gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from pko.extractors.base import Tree
from pko.extractors.runner import Extraction, extract_all
from pko.git.repo import GitRepo


@dataclass(frozen=True)
class TargetRepo:
    repo: GitRepo
    sha: str
    branch: str
    tree: Tree
    extraction: Extraction


def load_target(repo: GitRepo, branch: str | None = None) -> TargetRepo:
    """Разобрать код целевого репозитория на конкретном коммите.

    Принимает уже открытый `GitRepo` (по локальному пути или по зеркалу SSH —
    это решает `cli._open_repo`, здесь не дублируется), а не сырой путь.
    Коммит фиксируется явно, а не рабочее дерево: сравнение плана с кодом
    должно указывать на воспроизводимую версию, а не на то, что лежало в
    рабочей копии в момент запуска.
    """
    resolved_branch = branch or repo.default_branch()
    sha = repo.resolve(resolved_branch)
    tree = Tree.at(repo, sha)
    extraction = extract_all(tree)
    return TargetRepo(
        repo=repo, sha=sha, branch=resolved_branch, tree=tree, extraction=extraction
    )
