"""Клон и обновление зеркала от лица пользователя.

PKO не хранит ключи и токены: аутентификация целиком отдана вашему ssh-agent и
ключам из `~/.ssh`. Клон делается как `--mirror`, то есть без рабочего дерева —
анализ физически не может ничего изменить, а повторный запуск обновляет
зеркало `fetch --prune` вместо повторного скачивания.

`BatchMode=yes` включён намеренно: без него git молча зависает на запросе пароля
или подтверждения host key, и запуск выглядит как «ничего не происходит».
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from pko.errors import GitError, SshAccessError
from pko.git.url import RepoRef, parse_repo_url
from pko.util.paths import harden_dir

DEFAULT_CACHE_ROOT = Path.home() / ".pko" / "repos"

_SSH_BATCH = "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15"


@dataclass
class MirrorInfo:
    """Где лежит зеркало и что о нём известно."""

    path: Path
    ref: RepoRef
    created: bool
    fetched: bool


def mirror_path(ref: RepoRef, cache_root: Path | None = None) -> Path:
    root = Path(cache_root) if cache_root else DEFAULT_CACHE_ROOT
    # User и port входят в идентичность кеша: один host/project/repo может
    # обслуживаться несколькими SSH endpoint'ами и учётными записями.
    endpoint = f"{ref.user}@{ref.port or 22}"
    return root / ref.host.lower() / endpoint / ref.project / ref.mirror_dirname


def ensure_mirror(
    url: str,
    cache_root: Path | None = None,
    fetch: bool = True,
    timeout: int = 900,
) -> MirrorInfo:
    """Склонировать зеркало при первом запуске, иначе обновить его."""
    ref = parse_repo_url(url)
    root = Path(cache_root) if cache_root else DEFAULT_CACHE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    _harden(root)
    dest = mirror_path(ref, root)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if not (dest / "HEAD").exists():
        _run_git(["clone", "--mirror", ref.url, str(dest)], timeout=timeout, ref=ref)
        _write_meta(dest, ref, cloned=True)
        return MirrorInfo(path=dest, ref=ref, created=True, fetched=True)

    _verify_origin(dest, ref, timeout)

    if fetch:
        _run_git(["-C", str(dest), "remote", "update", "--prune"], timeout=timeout, ref=ref)
        _write_meta(dest, ref, cloned=False)
        return MirrorInfo(path=dest, ref=ref, created=False, fetched=True)

    return MirrorInfo(path=dest, ref=ref, created=False, fetched=False)


def _verify_origin(dest: Path, requested: RepoRef, timeout: int) -> None:
    """Не позволить существующему каталогу незаметно подменить remote."""
    configured = _run_git(
        ["-C", str(dest), "config", "--get", "remote.origin.url"],
        timeout=timeout,
        ref=requested,
    ).strip()
    try:
        actual = parse_repo_url(configured)
    except Exception as exc:
        raise GitError(
            f"Зеркало {dest} содержит некорректный origin URL.",
            "Удалите этот каталог кеша и повторите запуск.",
        ) from exc
    if actual.normalized_url != requested.normalized_url:
        raise GitError(
            f"Зеркало {dest} относится к другому remote: {actual.normalized_url}",
            f"Ожидался {requested.normalized_url}. Удалите конфликтующий каталог кеша.",
        )


def _harden(root: Path) -> None:
    """Корпоративный код оседает в кеше — каталог должен быть доступен только владельцу."""
    harden_dir(root)


def _write_meta(dest: Path, ref: RepoRef, cloned: bool) -> None:
    meta = {
        "url": ref.url,
        "normalized_url": ref.normalized_url,
        "host": ref.host,
        "project": ref.project,
        "repo": ref.repo,
        "last_action": "clone" if cloned else "fetch",
        "last_action_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        (dest / "pko-meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _run_git(args: list[str], timeout: int, ref: RepoRef) -> str:
    env = dict(os.environ)
    env.setdefault("GIT_SSH_COMMAND", _SSH_BATCH)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    try:
        proc = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SshAccessError(
            f"Git не ответил за {timeout} с при обращении к {ref.host}.",
            "Проверьте подключение к корпоративной сети или увеличьте --network-timeout.",
        ) from exc

    if proc.returncode == 0:
        return proc.stdout

    raise _classify(proc.stderr or proc.stdout, ref)


def _classify(stderr: str, ref: RepoRef) -> GitError:
    """Превратить сообщение git в понятную человеку причину отказа."""
    text = (stderr or "").strip()
    low = text.lower()
    tail = text[-400:]

    if "host key verification failed" in low or "no matching host key" in low:
        return SshAccessError(
            f"Хост {ref.host} не подтверждён в known_hosts.",
            f"Выполните: ssh-keyscan -p {ref.port or 22} {ref.host} >> ~/.ssh/known_hosts "
            "и убедитесь, что отпечаток совпадает с корпоративным.",
        )
    if "permission denied" in low or "publickey" in low or "authentication failed" in low:
        return SshAccessError(
            f"SSH-доступ к {ref.slug} отклонён.",
            "Добавьте ключ в агент: ssh-add ~/.ssh/id_rsa — и проверьте права на репозиторий "
            f"командой ssh -T {ref.user}@{ref.host}.",
        )
    if "could not resolve hostname" in low or "network is unreachable" in low or (
        "connection timed out" in low
    ):
        return SshAccessError(
            f"Хост {ref.host} недоступен.",
            "Похоже, нет подключения к корпоративной сети — включите VPN и повторите.",
        )
    if "repository not found" in low or "does not appear to be a git repository" in low:
        return SshAccessError(
            f"Репозиторий не найден: {ref.slug}",
            "Проверьте ссылку в Bitbucket — возможно, изменился проект или имя репозитория.",
        )
    if "terminal prompts disabled" in low or "could not read username" in low:
        return SshAccessError(
            "Git запросил интерактивный ввод, но PKO работает без запросов.",
            "Используйте SSH-ссылку и ключ в ssh-agent вместо логина и пароля.",
        )
    return GitError(f"Не удалось получить репозиторий {ref.slug}.", tail)
