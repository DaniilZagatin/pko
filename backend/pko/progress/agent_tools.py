"""Инструменты единого агента: read-only доступ к презентации и к дереву репозитория.

Четыре инструмента — `read_slides`, `list_files`, `read_file`, `search` — и
ничего больше: нет ни bash, ни write-операций, поэтому набор инструментов
физически не может изменить репозиторий, только прочитать его на
зафиксированном коммите (презентация и так неизменяемый вход). Секреты
(`*_key`/`*_token`/`*_secret`/`*_password`) маскируются в выдаче,
`.env*`/`*.pem`/`*.key` не читаются вовсе.
"""

from __future__ import annotations

import fnmatch
import re
import time
from dataclasses import dataclass
from typing import Any

from pko.extractors.base import Tree, is_vendor
from pko.progress.pptx_reader import Slide, render_slide

MAX_LIST_FILES = 400
MAX_READ_LINES = 400
MAX_SLIDES = 30
SEARCH_MAX_MATCHES = 200
SEARCH_TIMEOUT_SECONDS = 5.0
MAX_SEARCH_LINE_CHARS = 2000

_BLOCKED_NAME_PREFIXES = (".env",)
_BLOCKED_NAME_SUFFIXES = (".pem", ".key")

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b\w*(?:key|token|secret|password)\w*\s*[:=]\s*)([\"']?)([^\s\"']{4,})\2"
)

# Грубая, но дешёвая проверка на классический ReDoS-паттерн: повторяющаяся
# группа, внутри которой уже есть свой повтор — "(a+)+", "(\w*)+" и т.п.
_CATASTROPHIC_SHAPE = re.compile(r"\([^()]*[+*?][^()]*\)[+*]")

# Нативные OpenAI-совместимые схемы тулов (`tools=[...]` в запросе) — один в
# один повторяют сигнатуры и дефолты методов `ToolBox` ниже.
TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_slides",
            "description": "Посмотреть текст слайдов презентации ещё раз (уже был дан в начале сессии).",
            "parameters": {
                "type": "object",
                "properties": {
                    "offset": {"type": "integer", "description": "С какого слайда по порядку продолжить (постранично)", "default": 1},
                    "limit": {"type": "integer", "description": f"Сколько слайдов вернуть (не больше {MAX_SLIDES})", "default": MAX_SLIDES},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Список файлов репозитория на этом коммите (vendor-каталоги скрыты).",
            "parameters": {
                "type": "object",
                "properties": {
                    "glob": {"type": "string", "description": "Шаблон имени файла или пути, например *.py", "default": "*"},
                    "offset": {"type": "integer", "description": "С какого файла продолжить (постранично)", "default": 1},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Содержимое файла построчно, с номерами строк. Секреты маскируются, .env/.pem/.key не читаются.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Путь к файлу, как он вернулся из list_files/search"},
                    "offset": {"type": "integer", "description": "Строка, с которой начать", "default": 1},
                    "limit": {"type": "integer", "description": f"Сколько строк вернуть (не больше {MAX_READ_LINES})", "default": MAX_READ_LINES},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Regex-поиск по файлам репозитория на этом коммите.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Регулярное выражение для поиска"},
                    "glob": {"type": "string", "description": "Ограничить поиск файлами по шаблону", "default": "*"},
                    "offset": {"type": "integer", "description": "С какого совпадения продолжить (постранично)", "default": 1},
                },
                "required": ["pattern"],
            },
        },
    },
]


@dataclass
class ToolResult:
    ok: bool
    content: str
    meta: dict[str, Any]


class ToolBox:
    """Инструменты над презентацией и `Tree` — снимком репозитория на коммите."""

    def __init__(self, tree: Tree, slides: list[Slide] | None = None):
        self.tree = tree
        self._files = [p for p in tree.files if not is_vendor(p)]
        self._slides = list(slides) if slides else []

    def call(self, name: str, args: dict[str, Any] | None) -> ToolResult:
        args = args if isinstance(args, dict) else {}
        if name == "read_slides":
            return self.read_slides(offset=_as_int(args.get("offset"), 1), limit=_as_int(args.get("limit"), MAX_SLIDES))
        if name == "list_files":
            return self.list_files(glob=str(args.get("glob") or "*"), offset=_as_int(args.get("offset"), 1))
        if name == "read_file":
            return self.read_file(
                path=str(args.get("path") or ""),
                offset=_as_int(args.get("offset"), 1),
                limit=_as_int(args.get("limit"), MAX_READ_LINES),
            )
        if name == "search":
            return self.search(
                pattern=str(args.get("pattern") or ""),
                glob=str(args.get("glob") or "*"),
                offset=_as_int(args.get("offset"), 1),
            )
        return ToolResult(False, f"неизвестный инструмент: {name!r}. Доступны: read_slides, list_files, read_file, search", {})

    def read_slides(self, offset: int = 1, limit: int = MAX_SLIDES) -> ToolResult:
        limit = max(1, min(limit, MAX_SLIDES))
        page, has_more = _paginate(self._slides, offset, limit)
        if not page:
            return ToolResult(True, "(слайдов нет или offset за пределами презентации)", {})
        content = "\n\n".join(render_slide(s) for s in page)
        if has_more:
            content += f"\n\n... есть ещё слайды, offset={offset + limit} для продолжения"
        return ToolResult(True, content, {"count": len(page), "total": len(self._slides)})

    def list_files(self, glob: str = "*", offset: int = 1) -> ToolResult:
        matched = _glob_filter(self._files, glob)
        page, has_more = _paginate(matched, offset, MAX_LIST_FILES)
        content = "\n".join(page) if page else "(ничего не найдено)"
        if has_more:
            content += f"\n... есть ещё файлы, offset={offset + MAX_LIST_FILES} для продолжения"
        return ToolResult(True, content, {"count": len(page), "total": len(matched)})

    def read_file(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> ToolResult:
        path = path.strip()
        if path not in self._files:
            return ToolResult(False, f"файл не найден на этом коммите: {path}", {})
        if _is_blocked(path):
            return ToolResult(False, f"чтение файла запрещено (похоже на секрет): {path}", {})
        text = self.tree.read(path)
        if text is None:
            return ToolResult(False, f"файл не читается: {path}", {})

        lines = text.splitlines()
        limit = max(1, min(limit, MAX_READ_LINES))
        offset = max(1, offset)
        window = lines[offset - 1: offset - 1 + limit]
        if not window:
            return ToolResult(True, "(пусто или offset за пределами файла)", {"total_lines": len(lines)})
        numbered = "\n".join(
            f"{i}: {_mask_secrets(line)}" for i, line in enumerate(window, start=offset)
        )
        if offset - 1 + limit < len(lines):
            numbered += f"\n... есть ещё строки (всего {len(lines)}), offset={offset + limit} для продолжения"
        return ToolResult(True, numbered, {"total_lines": len(lines)})

    def search(self, pattern: str, glob: str = "*", offset: int = 1) -> ToolResult:
        pattern = pattern.strip()
        if not pattern:
            return ToolResult(False, "пустой паттерн поиска", {})
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            return ToolResult(False, f"некорректный regex: {exc}", {})
        if _CATASTROPHIC_SHAPE.search(pattern):
            return ToolResult(
                False,
                "паттерн похож на катастрофический backtracking (вложенный повтор внутри "
                "повторяющейся группы) — упростите regex",
                {},
            )

        matched_files = _glob_filter(self._files, glob)
        hits: list[str] = []
        deadline = time.monotonic() + SEARCH_TIMEOUT_SECONDS
        truncated = False
        for path in matched_files:
            if time.monotonic() > deadline:
                truncated = True
                break
            text = self.tree.read(path)
            if text is None:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if len(line) > MAX_SEARCH_LINE_CHARS:
                    continue
                if compiled.search(line):
                    hits.append(f"{path}:{i}: {_mask_secrets(line.strip())[:200]}")
                    if len(hits) >= SEARCH_MAX_MATCHES * 4:
                        truncated = True
                        break
            if truncated:
                break

        page, has_more = _paginate(hits, offset, SEARCH_MAX_MATCHES)
        content = "\n".join(page) if page else "(совпадений нет)"
        if has_more:
            content += f"\n... есть ещё совпадения, offset={offset + SEARCH_MAX_MATCHES} для продолжения"
        if truncated:
            content += "\n(поиск остановлен раньше срока — сузьте паттерн или glob)"
        return ToolResult(True, content, {"count": len(page)})


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _glob_filter(paths: list[str], pattern: str) -> list[str]:
    if pattern in ("", "*"):
        return paths
    return [
        p for p in paths
        if fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(p.rsplit("/", 1)[-1], pattern)
    ]


def _paginate(items: list[Any], offset: int, limit: int) -> tuple[list[Any], bool]:
    offset = max(1, offset)
    start = offset - 1
    page = items[start: start + limit]
    return page, start + limit < len(items)


def _is_blocked(path: str) -> bool:
    base = path.rsplit("/", 1)[-1].lower()
    return base.startswith(_BLOCKED_NAME_PREFIXES) or base.endswith(_BLOCKED_NAME_SUFFIXES)


def _mask_secrets(text: str) -> str:
    return _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}***{m.group(2)}", text)
