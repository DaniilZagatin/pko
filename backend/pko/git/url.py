"""Разбор ссылки на репозиторий Bitbucket.

Поддерживаются оба формата, которые выдаёт Bitbucket Server:
    ssh://git@stash.delta.sbrf.ru:7999/datacore_ai/ai-agent-deepresearch.git
    git@stash.delta.sbrf.ru:datacore_ai/ai-agent-deepresearch.git

HTTPS намеренно не поддерживается: PKO клонирует от лица пользователя по SSH и
не работает с паролями и токенами.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pko.errors import UrlError

_SSH_SCHEME = re.compile(
    r"^ssh://(?P<user>[^@/]+)@(?P<host>[^:/]+)(?::(?P<port>\d+))?/(?P<path>.+?)(?:\.git)?/?$"
)
_SCP_LIKE = re.compile(
    r"^(?P<user>[^@/\s]+)@(?P<host>[^:/\s]+):(?P<path>[^\s]+?)(?:\.git)?/?$"
)
_SAFE_USER = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_HOST = re.compile(r"^[A-Za-z0-9.-]+$")
_SAFE_PATH_PART = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class RepoRef:
    """Нормализованная ссылка на удалённый репозиторий."""

    url: str
    host: str
    port: int | None
    user: str
    project: str
    repo: str

    @property
    def slug(self) -> str:
        return f"{self.project}/{self.repo}"

    @property
    def mirror_dirname(self) -> str:
        return f"{self.repo}.git"

    @property
    def normalized_url(self) -> str:
        """Каноническая идентичность remote для кеша и проверки зеркала.

        SCP-like URL не содержит порт и потому нормализуется к стандартному 22.
        Явный ``:22`` и неявный порт считаются одним remote, но другой SSH user
        или другой порт — уже другой источник кода.
        """
        return (
            f"ssh://{self.user}@{self.host.lower()}:{self.port or 22}/"
            f"{self.project}/{self.repo}.git"
        )


def parse_repo_url(url: str) -> RepoRef:
    """Разобрать SSH-ссылку. Бросает `UrlError` с подсказкой для других схем."""
    raw = (url or "").strip()
    if not raw:
        raise UrlError("Ссылка на репозиторий пустая.", "Передайте SSH-ссылку из Bitbucket.")

    low = raw.lower()
    if low.startswith(("http://", "https://")):
        raise UrlError(
            "HTTPS-ссылки не поддерживаются.",
            "Скопируйте в Bitbucket ссылку вида "
            "ssh://git@host:7999/project/repo.git — PKO клонирует от вашего имени по SSH.",
        )

    m = _SSH_SCHEME.match(raw) or _SCP_LIKE.match(raw)
    if not m:
        raise UrlError(
            f"Не удалось разобрать ссылку: {raw}",
            "Ожидается ssh://git@host:7999/project/repo.git или git@host:project/repo.git",
        )

    path = m.group("path").strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise UrlError(
            f"В ссылке нет проекта и репозитория: {raw}",
            "Путь должен содержать проект и репозиторий: /project/repo.git",
        )

    user = m.group("user")
    host = m.group("host")
    if not _SAFE_USER.fullmatch(user) or not _SAFE_HOST.fullmatch(host):
        raise UrlError("SSH-ссылка содержит недопустимые user или host.")
    if any(p in {".", ".."} or not _SAFE_PATH_PART.fullmatch(p) for p in parts):
        raise UrlError(
            "Путь репозитория содержит недопустимый сегмент.",
            "Используйте SSH-ссылку, скопированную из Bitbucket без дополнительных аргументов.",
        )

    repo = parts[-1]
    if repo.endswith(".git"):
        repo = repo[: -len(".git")]
    project = "-".join(parts[:-1])

    port_raw = m.groupdict().get("port")
    return RepoRef(
        url=raw,
        host=host,
        port=int(port_raw) if port_raw else None,
        user=user,
        project=project,
        repo=repo,
    )
