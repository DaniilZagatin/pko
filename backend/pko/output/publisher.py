"""Запись и публикация отчётов.

Сначала всё пишется во временный каталог и проверяется, и только потом
заменяет опубликованные файлы. Перед первой заменой делается резервная копия:
в каталоге лежат эталоны ручной работы, и потерять их из-за неудачного прогона
нельзя. Файл стандарта не трогается никогда.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pko.errors import PkoError

# Что чем заменяется при публикации: имя артефакта → файл в каталоге отчётов.
PUBLISH_MAP = {
    "taxonomy": "taxonomy_v1_1.html",
    "passports": "passports_v1_1.html",
    "diff": "diff_v1_v2.md",
    "comparison": "passports_comparison_v1_v2.html",
}

PROTECTED = {"autonomous_process_standard_v1_1.md"}


@dataclass
class WrittenFile:
    kind: str
    path: Path


def write_outputs(out_dir: Path, files: dict[str, tuple[str, str]]) -> list[WrittenFile]:
    """Транзакционно записать полный комплект отчётов.

    Сначала все файлы создаются и валидируются на том же filesystem. Если хотя
    бы один replace не удался, восстанавливается весь прежний комплект.
    """
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    _validate_names(name for name, _ in files.values())

    with tempfile.TemporaryDirectory(prefix=".pko-stage-", dir=out_dir.parent) as raw:
        stage = Path(raw)
        staged: list[tuple[str, Path, Path]] = []
        for kind, (name, content) in files.items():
            source = stage / name
            source.write_text(content, encoding="utf-8")
            staged.append((kind, source, out_dir / name))
        _validate_staged([source for _, source, _ in staged])
        _commit_staged([(source, target) for _, source, target in staged])

    return [WrittenFile(kind=kind, path=target) for kind, _, target in staged]


def publish(
    written: list[WrittenFile], target_dir: Path, backup: bool = True, force: bool = False
) -> list[str]:
    """Заменить опубликованные отчёты. Возвращает список выполненных действий.

    Публикация перезаписывает файлы по фиксированным именам, поэтому каталог должен
    быть тем самым: если ни одного из этих имён там нет, а каталог не пуст, это почти
    наверняка не то место, и запись прекращается.
    """
    target_dir = Path(target_dir)
    if not force and target_dir.exists():
        existing = {p.name for p in target_dir.iterdir()}
        if existing and not (existing & set(PUBLISH_MAP.values())):
            raise PkoError(
                f"Каталог {target_dir} не похож на каталог отчётов PKO.",
                "Ожидались файлы " + ", ".join(sorted(PUBLISH_MAP.values()))
                + ". Укажите верный --publish-dir.",
            )
    target_dir.mkdir(parents=True, exist_ok=True)
    actions: list[str] = []

    selected: list[tuple[WrittenFile, str]] = []
    for item in written:
        target_name = PUBLISH_MAP.get(item.kind)
        if target_name and target_name not in PROTECTED:
            selected.append((item, target_name))
    _validate_names(name for _, name in selected)

    with tempfile.TemporaryDirectory(prefix=".pko-publish-", dir=target_dir) as raw:
        stage = Path(raw)
        staged: list[tuple[Path, Path]] = []
        for item, target_name in selected:
            source = stage / target_name
            shutil.copy2(item.path, source)
            staged.append((source, target_dir / target_name))
        _validate_staged([source for source, _ in staged])

        # Постоянный .bak создаётся до commit, но сами опубликованные файлы ещё
        # не меняются. При сбое commit `_commit_staged` вернёт весь старый набор.
        if backup:
            for _, target in staged:
                if target.exists():
                    backup_path = target.with_suffix(target.suffix + ".bak")
                    if not backup_path.exists():
                        shutil.copy2(target, backup_path)
                        actions.append(f"резервная копия: {backup_path.name}")
        _commit_staged(staged)

    actions.extend(f"опубликовано: {target.name}" for _, target in staged)
    return actions


def _validate_names(names) -> None:
    seen: set[str] = set()
    for name in names:
        if Path(name).name != name or name in {"", ".", ".."}:
            raise PkoError(f"Недопустимое имя выходного файла: {name}")
        if name in seen:
            raise PkoError(f"Дублирующееся имя выходного файла: {name}")
        seen.add(name)


def _validate_staged(paths: list[Path]) -> None:
    """Проверить весь stage до первого изменения целевого набора."""
    for path in paths:
        try:
            if not path.is_file() or path.stat().st_size == 0:
                raise PkoError(f"Сгенерирован пустой артефакт: {path.name}")
            path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            raise PkoError(f"Артефакт {path.name} не является UTF-8 текстом.") from exc
        except OSError as exc:
            raise PkoError(f"Не удалось проверить артефакт {path.name}: {exc}") from exc


def _replace(source: Path, target: Path) -> None:
    """Отдельная точка атомарной замены, в том числе для fault-injection тестов."""
    source.replace(target)


def _commit_staged(staged: list[tuple[Path, Path]]) -> None:
    """Заменить набор файлов с полным rollback при ошибке любого replace."""
    if not staged:
        return
    parent = staged[0][1].parent
    for _, target in staged:
        if target.parent != parent:
            raise PkoError("Все файлы одной транзакции должны иметь общий каталог назначения.")
    parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=".pko-rollback-", dir=parent) as raw:
        rollback = Path(raw)
        previous: dict[Path, Path | None] = {}
        for index, (_, target) in enumerate(staged):
            if target.exists():
                saved = rollback / f"{index}-{target.name}"
                shutil.copy2(target, saved)
                previous[target] = saved
            else:
                previous[target] = None

        try:
            for source, target in staged:
                _replace(source, target)
        except OSError as exc:
            restore_errors: list[str] = []
            for target, saved in previous.items():
                try:
                    if saved is None:
                        target.unlink(missing_ok=True)
                    else:
                        restore = rollback / (saved.name + ".restore")
                        shutil.copy2(saved, restore)
                        _replace(restore, target)
                except OSError as restore_exc:
                    restore_errors.append(f"{target.name}: {restore_exc}")
            detail = f"Не удалось атомарно обновить комплект отчётов: {exc}"
            if restore_errors:
                detail += "; ошибки восстановления: " + "; ".join(restore_errors)
            raise PkoError(detail, "Предыдущий комплект сохранён в backup/rollback насколько возможно.") from exc
