"""Чтение целевого репозитория на зафиксированном коммите.

Переиспользует read-only git-обвязку и детерминированные экстракторы PKO без
изменений: те же гарантии («без мутирующих команд», факты со ссылкой
path:line) нужны и здесь — сопоставление плана с кодом строится на тех же
фактах, что и паспорта Gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pko.extractors.base import FileTree, Tree
from pko.extractors.runner import Extraction, extract_all
from pko.git.remote import DEFAULT_CACHE_ROOT, ensure_mirror
from pko.git.repo import GitRepo
from pko.git.url import parse_repo_url


@dataclass(frozen=True)
class TargetRepo:
    """Снимок целевых материалов, из которых агент берёт evidence.

    `repo`/`sha`/`branch` осмысленны только когда источник — git; для
    материалов, загруженных без репозитория (`progress/local_source.py`),
    `repo=None`, `sha=""`, `branch=""` — не заглушка ради заглушки: они
    попадают только в `meta["commit"]`/`meta["branch"]` отчёта, где пустое
    значение уже отображается как `—`/пусто. `tree` — `FileTree`, не
    обязательно git-`Tree`: агенту и `extract_all` (см. `FileTree`) без
    разницы, что стоит за списком файлов и чтением по пути.
    """

    repo: GitRepo | None
    sha: str
    branch: str
    tree: FileTree
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


def repo_name(path: Path) -> str:
    """Имя репозитория для ссылки на реализацию: без `.git` и никогда не пустое."""
    name = path.name.removesuffix(".git")
    return name or path.parent.name or "repo"


def open_repo_source(
    source: str,
    branch: str | None = None,
    cache_root: Path | None = None,
    no_fetch: bool = False,
    network_timeout: int = 900,
) -> tuple[GitRepo, str]:
    """Открыть репозиторий по одной строке: локальный путь или SSH-ссылка.

    Для веб-формы с одним полем «репозиторий» — CLI различает `--repo-path` и
    `url` явно двумя флагами (`cli._open_repo`, не тронут), здесь то же самое
    решается автоопределением: существующий путь на диске побеждает разбор
    как SSH-ссылки, потому что путь либо существует, либо нет, а ссылку можно
    трактовать двусмысленно только в теории.
    """
    candidate = Path(source).expanduser()
    if candidate.exists():
        resolved = candidate.resolve()
        return GitRepo(resolved, timeout=network_timeout), repo_name(resolved)

    ref = parse_repo_url(source)
    info = ensure_mirror(
        source,
        cache_root=(cache_root or DEFAULT_CACHE_ROOT),
        fetch=not no_fetch,
        timeout=network_timeout,
    )
    return GitRepo(info.path, timeout=network_timeout), ref.repo
