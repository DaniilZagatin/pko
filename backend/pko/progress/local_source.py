"""Загруженные файлы (включая ZIP-архивы) как источник evidence — с Git-репозиторием
или вместо него.

`ToolBox` (`progress/agent_tools.py`) и весь `extract_all`
(`extractors/runner.py`) трогают только `tree.files`/`tree.read(path)`/
`tree.match(...)` — ни разу не обращаются к `Tree.repo`/`Tree.sha` напрямую
(см. `extractors.base.FileTree`). Поэтому не-git источник — не отдельный
параллельный слой, а второй поставщик того же контракта: агенту после сборки
`TargetRepo` физически всё равно, что за ним стоит, git это, ZIP или файлы
россыпью — и не важно, единственный это источник или дополнение к репозиторию.

Форма загрузки даёт два независимых необязательных поля — репозиторий и
файлы проекта (см. `web/app.py::create_analysis`) — а не выбор одного из
трёх вариантов: у пользователя может быть и репозиторий, и результат
эксперимента, не попавший в него (`metrics.json`, `results.csv`). Если
заполнены оба — `merge_with_uploads` строит один объединённый workspace, а
не два вердикта по отдельности.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from pko.errors import PkoError
from pko.extractors.base import FileTree
from pko.extractors.runner import extract_all
from pko.progress.target_repo import TargetRepo

# Тот же порядок величины, что у `GitRepo.read_text`'s `max_bytes` (2MB) —
# один файл. Отдельно — лимиты на весь workspace, которых у git нет (клон
# читает объекты по одному, а загрузка разворачивается на диск целиком
# сразу): без них ZIP-бомба (маленький архив, огромное содержимое) исчерпает
# диск ещё до того, как агент успеет что-то прочитать.
MAX_FILE_BYTES = 2_000_000
MAX_WORKSPACE_FILES = 5000
MAX_WORKSPACE_BYTES = 200_000_000


@dataclass
class LocalTree:
    """Тот же контракт чтения, что у `extractors.base.Tree`, но с диска."""

    root: Path
    files: list[str] = field(default_factory=list)
    _text_cache: dict[str, str | None] = field(default_factory=dict, repr=False)

    def read(self, path: str) -> str | None:
        if path in self._text_cache:
            return self._text_cache[path]
        full = self.root / path
        try:
            if full.stat().st_size > MAX_FILE_BYTES:
                result = None
            else:
                # errors="replace" — тот же выбор, что и у GitRepo.run() для
                # содержимого файлов: бинарник не должен ронять чтение,
                # только прийти нечитаемой кашей вместо текста.
                result = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            result = None
        self._text_cache[path] = result
        return result

    def match(self, suffixes: Iterable[str] = (), names: Iterable[str] = ()) -> list[str]:
        suffixes = tuple(suffixes)
        names = {n.lower() for n in names}
        out: list[str] = []
        for p in self.files:
            base = p.rsplit("/", 1)[-1].lower()
            if suffixes and p.lower().endswith(suffixes):
                out.append(p)
            elif names and base in names:
                out.append(p)
        return out


@dataclass
class CombinedTree:
    """Git-дерево + слой загруженных файлов поверх — для случая, когда
    заполнены и репозиторий, и файлы. `overlay` побеждает при совпадении
    пути: то, что пользователь загрузил явно, важнее версии из репозитория.
    """

    primary: FileTree
    overlay: LocalTree
    files: list[str] = field(init=False)

    def __post_init__(self) -> None:
        merged = dict.fromkeys(self.primary.files)
        merged.update(dict.fromkeys(self.overlay.files))
        self.files = sorted(merged)

    def read(self, path: str) -> str | None:
        if path in self.overlay.files:
            return self.overlay.read(path)
        return self.primary.read(path)

    def match(self, suffixes: Iterable[str] = (), names: Iterable[str] = ()) -> list[str]:
        suffixes = tuple(suffixes)
        names = {n.lower() for n in names}
        out: list[str] = []
        for p in self.files:
            base = p.rsplit("/", 1)[-1].lower()
            if suffixes and p.lower().endswith(suffixes):
                out.append(p)
            elif names and base in names:
                out.append(p)
        return out


def _safe_relative_path(name: str) -> str | None:
    """Нормализованный `/`-путь внутри workspace или `None`, если небезопасен.

    Отклоняет абсолютные пути, диски Windows (`C:\\...`) и любой `..`-сегмент
    после нормализации — не только буквальную подстроку `..`, которую можно
    обойти вариантами вроде `a/../../b`.
    """
    normalized = name.replace("\\", "/").strip("/")
    if not normalized or normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        return None
    parts = [p for p in normalized.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or not parts:
        return None
    return "/".join(parts)


class _Budget:
    def __init__(self) -> None:
        self.files = 0
        self.bytes = 0

    def add(self, size: int) -> None:
        self.files += 1
        self.bytes += size
        if self.files > MAX_WORKSPACE_FILES:
            raise PkoError(
                "Слишком много файлов в загруженных материалах.",
                hint=f"ограничение — {MAX_WORKSPACE_FILES} файлов",
            )
        if self.bytes > MAX_WORKSPACE_BYTES:
            raise PkoError(
                "Загруженные материалы слишком большие в сумме.",
                hint=f"ограничение — {MAX_WORKSPACE_BYTES // 1_000_000} МБ суммарно",
            )


def _write(dest: Path, relative: str, data: bytes, budget: _Budget) -> str:
    budget.add(len(data))
    target = dest / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative


def _extract_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo,
                         dest: Path, budget: _Budget) -> str:
    relative = _safe_relative_path(info.filename)
    if relative is None:
        raise PkoError(
            "ZIP-архив содержит небезопасный путь.",
            hint=f"элемент архива: {info.filename!r}",
        )
    return _write(dest, relative, archive.read(info), budget)


def _extract_uploads(uploads: list[tuple[str, bytes]], dest: Path, budget: _Budget) -> list[str]:
    """Записать загруженные файлы в `dest`; `.zip` среди них распаковывается
    (не хранится как нечитаемый бинарник), остальное — как есть."""
    indexed: list[str] = []
    for filename, data in uploads:
        if filename.lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    for info in archive.infolist():
                        if info.is_dir():
                            continue
                        indexed.append(_extract_zip_member(archive, info, dest, budget))
            except zipfile.BadZipFile as exc:
                raise PkoError(f"Не удалось прочитать ZIP-архив {filename!r}.", hint=str(exc)) from exc
            continue
        relative = _safe_relative_path(filename)
        if relative is None:
            raise PkoError("Недопустимое имя файла.", hint=f"получено: {filename!r}")
        indexed.append(_write(dest, relative, data, budget))
    return indexed


def build_empty_workspace(dest: Path) -> TargetRepo:
    """Ни репозиторий, ни файлы не предоставлены — не ошибка запроса: агент
    всё равно запускается, просто видит пустой снимок материалов.

    Никакого специального сообщения сюда не подставляется — что писать в
    вердикт при пустом evidence, агент решает сам через уже существующую
    инструкцию `_AGENT_SYSTEM` ("если подтверждения не нашёл — отправь
    NOT_STARTED с пустым evidence"): это тот же путь, что и при обычном
    "в репозитории ничего не нашлось", просто изначально пусто.
    """
    dest.mkdir(parents=True, exist_ok=True)
    tree = LocalTree(root=dest, files=[])
    return TargetRepo(repo=None, sha="", branch="", tree=tree, extraction=extract_all(tree))


def build_target_repo_from_uploads(uploads: list[tuple[str, bytes]], dest: Path) -> TargetRepo:
    """Только загруженные файлы, без репозитория — `TargetRepo.repo=None`.

    `sha`/`branch` пустые — не заглушка ради заглушки: `run_progress` кладёт
    их только в `meta["commit"]`/`meta["branch"]`, а там пустое значение уже
    отображается как `—`/пусто и в футере CLI-отчёта, и в dashboard (поле
    убрано из шапки раньше).
    """
    dest.mkdir(parents=True, exist_ok=True)
    budget = _Budget()
    files = _extract_uploads(uploads, dest, budget)
    if not files:
        raise PkoError("Не выбрано ни одного файла.", hint="загрузите хотя бы один файл")
    tree = LocalTree(root=dest, files=sorted(files))
    return TargetRepo(repo=None, sha="", branch="", tree=tree, extraction=extract_all(tree))


def merge_with_uploads(target: TargetRepo, uploads: list[tuple[str, bytes]], dest: Path) -> TargetRepo:
    """Репозиторий + дополнительные файлы поверх — заполнены оба поля формы.

    Извлечение (`extract_all`) считается заново на объединённом дереве:
    экстракторы должны видеть и файлы из репозитория, и дополнительные
    загруженные — иначе `find_unclaimed_paths`/агентные инструменты видели бы
    только часть материалов в зависимости от того, кто спрашивает.
    """
    dest.mkdir(parents=True, exist_ok=True)
    budget = _Budget()
    files = _extract_uploads(uploads, dest, budget)
    if not files:
        return target
    overlay = LocalTree(root=dest, files=sorted(files))
    combined = CombinedTree(primary=target.tree, overlay=overlay)
    return TargetRepo(
        repo=target.repo, sha=target.sha, branch=target.branch,
        tree=combined, extraction=extract_all(combined),
    )
