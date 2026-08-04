"""Read-only доступ к git-репозиторию.

Единственные мутирующие операции PKO — `clone --mirror` и `fetch` — живут в
`pko.git.remote`. Здесь работает жёсткий allowlist подкоманд: если код когда-нибудь
попытается сделать `checkout`, `reset` или `push`, вызов упадёт до запуска git.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from pko.errors import GitError

# Только команды, которые не меняют репозиторий и не создают рабочее дерево.
ALLOWED_SUBCOMMANDS = frozenset(
    {"log", "cat-file", "rev-parse", "ls-tree", "diff", "show", "for-each-ref", "rev-list"}
)

_SEP = "\x1f"
_REC = "\x1e"


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    date: str
    author: str
    subject: str

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1

    @property
    def short(self) -> str:
        return self.sha[:8]


@dataclass
class GitRepo:
    """Обёртка над локальным репозиторием: обычным клоном или bare-зеркалом."""

    path: Path
    timeout: int = 120
    _files_cache: dict[str, list[str]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.path = Path(self.path)
        if not self.path.exists():
            raise GitError(
                f"Каталог репозитория не найден: {self.path}",
                "Проверьте путь в --repo-path или дайте SSH-ссылку, чтобы PKO склонировал сам.",
            )
        if not (self.path / ".git").exists() and not (self.path / "HEAD").exists():
            raise GitError(
                f"Это не git-репозиторий: {self.path}",
                "Нужен обычный клон или bare-зеркало (каталог *.git).",
            )

    # --- низкий уровень ---------------------------------------------------
    def run(self, *args: str) -> str:
        if not args:
            raise GitError("Пустая git-команда.")
        if args[0] not in ALLOWED_SUBCOMMANDS:
            raise GitError(
                f"Подкоманда git запрещена в режиме чтения: {args[0]}",
                "Изменять репозиторий разрешено только модулю pko.git.remote.",
            )
        env = dict(os.environ)
        env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"})
        try:
            proc = subprocess.run(
                ["git", "-C", str(self.path), *args],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=self.timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # Обход истории большого репозитория не ограничен по числу коммитов,
            # поэтому упереться в таймаут — штатный исход. Он должен выглядеть как
            # понятная ошибка PKO, а не как трассировка стека.
            raise GitError(
                f"git {' '.join(args[:2])} не ответил за {self.timeout} с.",
                "Увеличьте --network-timeout или сузьте анализ флагом --max-versions.",
            ) from exc
        if proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args[:3])} завершился с кодом {proc.returncode}",
                proc.stderr.strip()[:400],
            )
        return proc.stdout

    # --- ссылки -----------------------------------------------------------
    def resolve(self, ref: str) -> str:
        """Найти коммит по имени ветки в клоне или зеркале."""
        candidates = [ref, f"refs/heads/{ref}", f"refs/remotes/origin/{ref}", f"origin/{ref}"]
        for cand in candidates:
            try:
                return self.run("rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}").strip()
            except GitError:
                continue
        raise GitError(
            f"Ветка или коммит не найдены: {ref}",
            f"Доступные ветки: {', '.join(self.branches()[:10]) or 'нет'}",
        )

    def branches(self) -> list[str]:
        out = self.run("for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes")
        return [line.strip() for line in out.splitlines() if line.strip()]

    def default_branch(self) -> str:
        for name in ("master", "main", "origin/master", "origin/main"):
            try:
                self.resolve(name)
                return name
            except GitError:
                continue
        names = self.branches()
        if not names:
            raise GitError("В репозитории нет веток.", "Возможно, зеркало скачано не полностью.")
        return names[0]

    # --- история ----------------------------------------------------------
    def first_parent(self, ref: str, limit: int | None = None) -> list[Commit]:
        """Коммиты основной линии от новых к старым."""
        args = [
            "log",
            "--first-parent",
            f"--format=%H{_SEP}%P{_SEP}%ad{_SEP}%an{_SEP}%s{_REC}",
            "--date=short",
        ]
        if limit:
            args.append(f"-n{limit}")
        args.append(ref)
        return self._parse_commits(self.run(*args))

    def first_commit_touching(self, pathspecs: list[str], rev: str = "HEAD") -> Commit | None:
        """Самый ранний коммит указанной ревизии, добавивший файл по маске (`*.py`).

        Ревизию обязательно передавать явно: без неё git смотрит собственный HEAD,
        то есть ветку по умолчанию, и при анализе другой ветки базовая версия
        бралась бы из чужой истории.
        """
        # `--max-count` применяется до `--reverse`, поэтому ограничивать вывод здесь нельзя:
        # с ним git вернул бы последний коммит вместо первого.
        args = [
            "log",
            "--reverse",
            "--diff-filter=A",
            f"--format=%H{_SEP}%P{_SEP}%ad{_SEP}%an{_SEP}%s{_REC}",
            "--date=short",
            rev,
            "--",
            *pathspecs,
        ]
        commits = self._parse_commits(self.run(*args))
        return commits[0] if commits else None

    def _parse_commits(self, raw: str) -> list[Commit]:
        commits: list[Commit] = []
        for chunk in raw.split(_REC):
            chunk = chunk.strip("\n")
            if not chunk.strip():
                continue
            parts = chunk.split(_SEP)
            if len(parts) < 5:
                continue
            sha, parents, date, author, subject = parts[:5]
            commits.append(
                Commit(
                    sha=sha.strip(),
                    parents=tuple(p for p in parents.split() if p),
                    date=date.strip(),
                    author=author.strip(),
                    subject=subject.strip(),
                )
            )
        return commits

    # --- содержимое -------------------------------------------------------
    def files(self, sha: str) -> list[str]:
        """Список файлов на коммите (с кешем — вызывается многими экстракторами)."""
        if sha not in self._files_cache:
            out = self.run("ls-tree", "-r", "--name-only", sha)
            self._files_cache[sha] = [line for line in out.splitlines() if line]
        return self._files_cache[sha]

    def read_text(self, sha: str, path: str, max_bytes: int = 2_000_000) -> str | None:
        """Содержимое файла на коммите. None — если файла нет или он слишком большой."""
        try:
            size_raw = self.run("cat-file", "-s", f"{sha}:{path}").strip()
            if size_raw.isdigit() and int(size_raw) > max_bytes:
                return None
            return self.run("cat-file", "-p", f"{sha}:{path}")
        except GitError:
            return None

    def changed_files(self, base: str, head: str) -> list[tuple[str, str]]:
        """Пары (статус, путь) между двумя коммитами."""
        out = self.run("diff", "--name-status", "--no-renames", base, head)
        rows: list[tuple[str, str]] = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                rows.append((parts[0].strip(), parts[-1].strip()))
        return rows
