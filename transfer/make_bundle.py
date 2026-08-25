#!/usr/bin/env python3
"""Каталог с изменёнными файлами — для переноса копированием.

Патч (`make_patch.py`) требует, чтобы у получателя был git и ровно тот же
базовый коммит. Там, где этого нет, проще перенести сами файлы: каталог
повторяет структуру репозитория, поэтому распаковка поверх установленного PKO
и есть применение изменений.

Правила те же, что и у патча: не переносим ключи, кеши, прогоны и материалы
конкретного проекта, а перед записью просматриваем содержимое на очевидные
секреты.

    python3 transfer/make_bundle.py
    python3 transfer/make_bundle.py --out /tmp/pko-bundle --label отчёты
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import time
from pathlib import Path

from make_patch import (  # общие правила: перечень исключений и поиск секретов
    ROOT,
    SECRET_MARKERS,
    PatchError,
    _git,
    _slug,
    is_excluded,
)

DEFAULT_OUT = Path(__file__).resolve().parent / "bundle"

# Расширения, которые смотрим на секреты. Двоичные файлы пропускаем: там
# проверка бессмысленна, а прочитать их как текст нельзя.
TEXT_SUFFIXES = (".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".sh",
                 ".json", ".cfg", ".ini", ".html")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build(Path(args.out), label=args.label, clean=not args.keep)
    except PatchError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    print(f"Каталог:  {result['dir']}")
    print(f"Файлов:   {result['files']} (новых {result['added']}, "
          f"изменённых {result['modified']})")
    print(f"Основан на коммите: {result['base']}")
    name = Path(result["dir"]).name
    print(f"\nУпаковать и перенести:\n"
          f"  tar -czf pko-изменения.tar.gz -C {Path(result['dir']).parent} {name}\n"
          f"\nНа принимающей стороне:\n"
          f"  tar -xzf pko-изменения.tar.gz\n"
          f"  cd {name} && shasum -a 256 -c SHA256SUMS\n"
          f"  cp -R files/. /path/to/pko/\n"
          f"  cd /path/to/pko && make test")
    return 0


def build(out_dir: Path, label: str = "", clean: bool = True) -> dict[str, object]:
    """Собрать каталог с изменёнными файлами и опись."""
    base = _git("rev-parse", "HEAD").strip()
    changes = _changes()
    if not changes:
        raise PatchError("рабочее дерево не отличается от HEAD — переносить нечего")

    # Каталог сборки не должен попасть в самого себя: без этого повторный
    # запуск упаковывал предыдущий результат, и число файлов росло с каждым
    # прогоном. Общий перечень исключений знает про `transfer/bundle`, но
    # `--out` может указывать и в другое место внутри репозитория.
    own = _relative_to_root(out_dir)
    changes = [(s, p) for s, p in changes if not (own and p.startswith(own))]

    deleted = [path for status, path in changes if status.startswith("D")]
    carried = [(status, path) for status, path in changes if not status.startswith("D")]
    if not carried:
        raise PatchError("после исключений переносить нечего")

    leaked = _scan(path for _status, path in carried)
    if leaked:
        raise PatchError(
            "в переносимых файлах найдено похожее на секрет:\n  " + "\n  ".join(leaked[:5])
            + "\nУберите значение из кода и соберите каталог заново."
        )

    if clean and out_dir.exists():
        shutil.rmtree(out_dir)
    files_root = out_dir / "files"
    files_root.mkdir(parents=True, exist_ok=True)

    digests: list[tuple[str, str]] = []
    for _status, path in carried:
        source = ROOT / path
        target = files_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        digests.append((_sha256(source), path))

    (out_dir / "SHA256SUMS").write_text(
        "".join(f"{digest}  files/{path}\n" for digest, path in sorted(digests, key=lambda x: x[1])),
        encoding="utf-8",
    )
    (out_dir / "MANIFEST.md").write_text(
        _manifest(base, carried, deleted, label), encoding="utf-8")

    return {
        "dir": str(out_dir),
        "base": base,
        "files": len(carried),
        "added": sum(1 for status, _ in carried if status.startswith("A")),
        "modified": sum(1 for status, _ in carried if status.startswith("M")),
        "deleted": deleted,
    }


def _relative_to_root(path: Path) -> str:
    """Путь каталога сборки относительно корня репозитория, если он внутри."""
    try:
        return str(path.resolve().relative_to(ROOT)) + "/"
    except ValueError:
        return ""


def _changes() -> list[tuple[str, str]]:
    """Изменённые и новые файлы: (статус, путь). Исключения уже применены."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="pko-bundle-") as raw:
        env = {"GIT_INDEX_FILE": str(Path(raw) / "index")}
        _git("read-tree", "HEAD", env=env)
        new_files = [
            p for p in _git("ls-files", "--others", "--exclude-standard").splitlines()
            if p.strip() and not is_excluded(p)
        ]
        if new_files:
            _git("add", "--intent-to-add", "--", *new_files, env=env)
        listing = _git("diff", "--name-status", "HEAD", env=env)

    out: list[tuple[str, str]] = []
    for line in listing.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        if not is_excluded(path):
            out.append((status, path))
    return sorted(out, key=lambda item: item[1])


def _scan(paths) -> list[str]:
    """Очевидные секреты в переносимых файлах.

    Здесь, в отличие от патча, читается содержимое целиком, а не строки diff:
    в каталог попадает весь файл, включая то, что не менялось.
    """
    found: list[str] = []
    for path in paths:
        if not str(path).endswith(TEXT_SUFFIXES):
            continue
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _TEST_ASSERTION_RE.search(line):
                continue
            for pattern in SECRET_MARKERS:
                # Шаблоны написаны для строк diff и ждут ведущий «+».
                if pattern.search("+" + line):
                    found.append(f"{path}:{number}: {line.strip()[:100]}")
                    break
    return found


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _manifest(base: str, carried: list[tuple[str, str]],
              deleted: list[str], label: str) -> str:
    added = [p for s, p in carried if s.startswith("A")]
    modified = [p for s, p in carried if s.startswith("M")]
    listing = "\n".join(
        f"- `{path}` — {'новый' if status.startswith('A') else 'изменён'}"
        for status, path in carried
    )
    removal = ""
    if deleted:
        removal = (
            "\n## Удалить вручную\n\n"
            "Копирование поверх не удаляет файлы, поэтому эти нужно убрать руками:\n\n"
            + "\n".join(f"- `{path}`" for path in deleted) + "\n"
        )
    return f"""# Изменения PKO {label}""".strip() + f"""

| Поле | Значение |
|---|---|
| Базовый коммит | `{base}` |
| Файлов | {len(carried)} (новых {len(added)}, изменённых {len(modified)}) |
| Собрано | {time.strftime('%Y-%m-%d %H:%M')} |

## Как перенести

Каталог `files/` повторяет структуру репозитория, поэтому копирование поверх
установленного PKO и есть применение изменений.

```bash
# на этой машине
tar -czf pko-изменения.tar.gz -C .. bundle

# на принимающей
tar -xzf pko-изменения.tar.gz
cd bundle
shasum -a 256 -c SHA256SUMS             # на Linux: sha256sum -c
cp -R files/. /path/to/pko/
cd /path/to/pko && make test
```

Суммы посчитаны по путям вида `files/…`, поэтому проверять их нужно из самого
каталога `bundle`, а не снаружи.

Базовый коммит указан для сверки: файлы собраны поверх `{base[:8]}`. Если на
принимающей стороне другая версия, копирование затрёт её изменения без
предупреждения — сравните версии до переноса.
{removal}
## Что вошло

{listing}

## Чего в каталоге нет

Ключи и `.env`, кеши `~/.pko`, каталоги прогонов (`pko-out/`, `bench/runs/`),
генерируемые тестовые репозитории и материалы конкретного проекта
(`intent_ai_advisor*.yaml`, `business_requirements_ai_advisor.md`). Перечень
исключений — в `transfer/make_patch.py`.
"""


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Собрать каталог с изменёнными файлами PKO для переноса копированием")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="куда положить каталог (по умолчанию transfer/bundle)")
    parser.add_argument("--label", default="", help="короткая пометка для описи")
    parser.add_argument("--keep", action="store_true",
                        help="не очищать каталог перед сборкой")
    return parser.parse_args(argv)


# Импортируется из `make_patch`, но нужен и здесь: строка, где секрет является
# предметом теста, не считается утечкой.
from make_patch import _TEST_ASSERTION as _TEST_ASSERTION_RE  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
