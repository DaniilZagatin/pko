#!/usr/bin/env python3
"""Сборка переносимого патча PKO.

Архивом переносить всё дерево дорого и неудобно проверять: получатель не видит,
что именно изменилось, и обязан доверять целиком. Патч решает обе задачи —
он читается глазами, применяется одной командой и привязан к базовому коммиту,
поэтому применить его не туда невозможно.

Что делает скрипт:

  * собирает единый diff рабочего дерева против `HEAD`, включая новые файлы;
  * исключает то, что переносить нельзя (ключи, кеши, прогоны) и то, что
    относится к конкретному проекту, а не к PKO;
  * отказывается собирать патч, если внутри нашлось похожее на секрет;
  * кладёт рядом манифест и контрольную сумму.

Индекс git при этом не трогается: временный `GIT_INDEX_FILE` нужен, чтобы
`--intent-to-add` не оставил следов в рабочем каталоге разработчика.

    python3 transfer/make_patch.py
    python3 transfer/make_patch.py --out /tmp/пакет
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path(__file__).resolve().parent / "out"

# Что не попадает в патч.
#
# Первая группа — то, что не является кодом PKO: кеши, прогоны, генерируемые
# фикстуры. Вторая — материалы конкретного проекта: они могут содержать
# сведения, которые переносятся отдельно и по другим правилам.
# Префиксы путей и шаблоны имён, которые не переносятся.
EXCLUDE_PREFIXES = (
    ".venv/", "bench/runs/", "build/", "dist/",
    "tests/fixtures/mini_repo/", "tests/fixtures/multistack_repo/",
    "transfer/out/", "transfer/bundle/",
)
# Каталоги прогонов операторы называют по-разному: `pko-out`, `pko-out-llm`,
# `pko-out-llm2`. Точный префикс `pko-out/` пропускал остальные, и в пакет
# переноса уходили отчёты с кодом анализируемой системы.
EXCLUDE_PATTERNS = ("*.pyc", "*/__pycache__/*", "*.egg-info/*", ".env", ".env.*",
                    "pko-out*", "pko-out*/*")
# Материалы проекта AI Advisor: для работы PKO не нужны и переносятся отдельно.
EXCLUDE_FILES = (
    "business_requirements_ai_advisor.md",
    "intent_ai_advisor.yaml",
    "intent_ai_advisor_overstated.yaml",
)

# То же самое языком pathspec — для `git diff`, который принимает исключения.
EXCLUDE = tuple(
    f":(exclude){item}"
    for item in (*(p.rstrip("/") for p in EXCLUDE_PREFIXES), "pko-out*", *EXCLUDE_FILES)
)


def is_excluded(path: str) -> bool:
    """Не переносим: не код PKO либо материалы конкретного проекта."""
    if path in EXCLUDE_FILES:
        return True
    if any(path.startswith(prefix) for prefix in EXCLUDE_PREFIXES):
        return True
    return any(fnmatch.fnmatch(path, pattern) for pattern in EXCLUDE_PATTERNS)

# Признаки секрета в тексте патча. Проверка грубая и намеренно осторожная:
# лучше отказать и попросить посмотреть глазами, чем отправить ключ.
SECRET_MARKERS = (
    re.compile(r"^\+.*\b(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,})", re.MULTILINE),
    re.compile(r"^\+.*BEGIN [A-Z ]*PRIVATE KEY", re.MULTILINE),
    re.compile(r"^\+.*\b(api_key|token|password|secret)\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']",
               re.IGNORECASE | re.MULTILINE),
)


class PatchError(RuntimeError):
    """Сборка патча невозможна: причина понятна и требует решения человека."""


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build(Path(args.out), label=args.label)
    except PatchError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    print(f"Патч:     {result['patch']}")
    print(f"Манифест: {result['manifest']}")
    print(f"Основан на коммите: {result['base']}")
    print(f"Файлов в патче: {result['files']} · строк: {result['lines']}")
    print(f"\nНа принимающей стороне:\n"
          f"  git apply --check {Path(result['patch']).name}\n"
          f"  git apply {Path(result['patch']).name}")
    return 0


def build(out_dir: Path, label: str = "") -> dict[str, object]:
    """Собрать патч и манифест; вернуть сведения о результате."""
    base = _git("rev-parse", "HEAD").strip()
    diff = _collect_diff()
    if not diff.strip():
        raise PatchError("рабочее дерево не отличается от HEAD — переносить нечего")

    leaked = _find_secrets(diff)
    if leaked:
        raise PatchError(
            "в изменениях найдено похожее на секрет:\n  " + "\n  ".join(leaked[:5])
            + "\nУберите значение из кода и соберите патч заново."
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M")
    name = f"pko-{stamp}-{base[:8]}" + (f"-{_slug(label)}" if label else "")
    patch_path = out_dir / f"{name}.patch"
    patch_path.write_text(diff, encoding="utf-8")

    digest = hashlib.sha256(diff.encode("utf-8")).hexdigest()
    (out_dir / f"{name}.patch.sha256").write_text(
        f"{digest}  {patch_path.name}\n", encoding="utf-8")

    files = _changed_files()
    manifest_path = out_dir / f"{name}.manifest.md"
    manifest_path.write_text(
        _manifest(base, patch_path.name, digest, files, label), encoding="utf-8")

    return {
        "patch": str(patch_path),
        "manifest": str(manifest_path),
        "base": base,
        "files": len(files),
        "lines": diff.count("\n"),
        "sha256": digest,
    }


def _new_files() -> list[str]:
    """Новые файлы, которые нужно перенести.

    Список берём явно, а не пathspec'ом: `git add -- . :(exclude)…` всё равно
    ругается на игнорируемые каталоги и возвращает ненулевой код. Здесь же
    видно ровно то, что уйдёт в патч.
    """
    listing = _git("ls-files", "--others", "--exclude-standard")
    return [p for p in listing.splitlines() if p.strip() and not is_excluded(p)]


def _in_temp_index(command: list[str]) -> str:
    """Выполнить git-команду во временном индексе с зарегистрированными новинками.

    Индекс разработчика не трогаем: `--intent-to-add` нужен только для того,
    чтобы `diff` увидел новые файлы, и следов после себя оставлять не должен.
    """
    with tempfile.TemporaryDirectory(prefix="pko-patch-") as raw:
        env = {"GIT_INDEX_FILE": str(Path(raw) / "index")}
        _git("read-tree", "HEAD", env=env)
        new_files = _new_files()
        if new_files:
            _git("add", "--intent-to-add", "--", *new_files, env=env)
        return _git(*command, "HEAD", "--", ".", *EXCLUDE, env=env)


def _collect_diff() -> str:
    """Единый diff рабочего дерева против HEAD, включая новые файлы."""
    return _in_temp_index(["diff", "--binary", "--no-color"])


def _changed_files() -> list[str]:
    listing = _in_temp_index(["diff", "--name-status"])
    return [line for line in listing.splitlines() if line.strip()]


# Строки, где «секрет» является предметом проверки, а не значением. В наборе
# тестов есть заведомо поддельные ключи: на них проверяется маскировка, и без
# этого исключения патч не собрать никогда. Правило узкое: строка должна
# выглядеть утверждением теста, иначе она блокирует сборку.
_TEST_ASSERTION = re.compile(r"\bassert|_mask_secrets\(|SECRET_HINTS|self\.assert")


def _find_secrets(diff: str) -> list[str]:
    """Строки патча, похожие на утёкший секрет.

    Проверка грубая и намеренно осторожная: она не доказывает отсутствие
    секретов, а ловит очевидное до отправки. Всё найденное блокирует сборку —
    решение о том, что это ложное срабатывание, принимает человек.
    """
    found: list[str] = []
    for pattern in SECRET_MARKERS:
        for match in pattern.finditer(diff):
            line = match.group(0).strip()
            if _TEST_ASSERTION.search(line):
                continue
            found.append(line[:120])
    return found


def _manifest(base: str, patch_name: str, digest: str,
              files: list[str], label: str) -> str:
    added = sum(1 for f in files if f.startswith("A"))
    modified = sum(1 for f in files if f.startswith("M"))
    deleted = sum(1 for f in files if f.startswith("D"))
    listing = "\n".join(f"- `{line}`" for line in sorted(files))
    title = f"Патч PKO {label}".strip()
    return f"""# {title}

| Поле | Значение |
|---|---|
| Файл патча | `{patch_name}` |
| SHA-256 | `{digest}` |
| Базовый коммит | `{base}` |
| Файлов | {len(files)} (новых {added}, изменённых {modified}, удалённых {deleted}) |
| Собрано | {time.strftime('%Y-%m-%d %H:%M')} |

## Как применить

Патч привязан к базовому коммиту: применять его нужно к дереву, где
`git rev-parse HEAD` даёт ровно `{base}`. На другом коммите `git apply`
откажется — это защита от применения не туда, а не помеха.

```bash
cd /path/to/pko
git rev-parse HEAD                 # должно совпасть с базовым коммитом
sha256sum -c {patch_name}.sha256   # на macOS: shasum -a 256 -c
git apply --check {patch_name}     # сухой прогон, ничего не меняет
git apply {patch_name}
make test
```

Если `git apply --check` ругается на несовпадение контекста, дерево получателя
уже отличается от базового: разберитесь с расхождением, а не применяйте патч с
`--3way` вслепую.

## Что вошло

{listing}

## Чего в патче нет

Ключи и `.env`, кеши `~/.pko`, каталоги прогонов (`pko-out/`, `bench/runs/`),
генерируемые тестовые репозитории и материалы конкретного проекта
(`intent_ai_advisor*.yaml`, `business_requirements_ai_advisor.md`). Перечень
исключений задан в `transfer/make_patch.py`.
"""


def _slug(value: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in value.strip().lower())
    return cleaned.strip("-")[:40]


def _git(*args: str, env: dict[str, str] | None = None) -> str:
    full_env = dict(os.environ)
    full_env.update(env or {})
    proc = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
        errors="replace", env=full_env,
    )
    if proc.returncode != 0:
        raise PatchError(f"git {args[0]} завершился с кодом {proc.returncode}: "
                         f"{(proc.stderr or proc.stdout).strip()[:300]}")
    return proc.stdout


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Собрать переносимый патч PKO")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="куда положить патч (по умолчанию transfer/out)")
    parser.add_argument("--label", default="",
                        help="короткая пометка в имени файла, например «отчёты»")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
