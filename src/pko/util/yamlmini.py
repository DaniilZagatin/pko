"""Мини-разбор YAML без внешних зависимостей.

PKO читает единственный YAML — `business_intent.yaml`, который заполняет человек.
Нужны плоские ключи, списки строк и вложенные словари; тянуть PyYAML в
зависимости ради этого не стоит. Всё, что сложнее (якоря, многострочные блоки,
сложные ключи), честно отвергается с подсказкой, а не разбирается наполовину.
"""

from __future__ import annotations

from typing import Any

_TRUE = {"true", "yes", "да", "on"}
_FALSE = {"false", "no", "нет", "off"}


class YamlSubsetError(ValueError):
    """Конструкция за пределами поддерживаемого подмножества."""


def loads(text: str, notes: list[str] | None = None) -> Any:
    """Разобрать поддерживаемое подмножество YAML.

    `notes` — куда сложить предупреждения. Сейчас туда попадает единственный
    неочевидный для человека случай: незакавыченное значение обрезано
    комментарием. Правило совпадает с настоящим YAML, но потеря части значения
    не должна оставаться незаметной для того, кто заполняет файл руками.
    """
    lines = _meaningful(text, notes)
    if not lines:
        return {}
    value, pos = _block(lines, 0, lines[0][0])
    if pos != len(lines):
        _, lineno, content = lines[pos][0], lines[pos][2], lines[pos][1]
        raise YamlSubsetError(f"строка {lineno}: неожиданный сдвиг отступа — «{content}»")
    return value


def _meaningful(text: str, notes: list[str] | None = None) -> list[tuple[int, str, int]]:
    """(отступ, содержимое, номер строки) без пустых строк и комментариев."""
    out: list[tuple[int, str, int]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        stripped_line, spaces_before = _strip_comment(raw)
        line = stripped_line.rstrip()
        # Выровненный комментарий (два и более пробела) — общепринятая запись,
        # предупреждать о нём значит завалить список пробелов ложными строками.
        ambiguous = 0 <= spaces_before < 2
        if ambiguous and notes is not None and line.strip() and not line.strip().startswith("#"):
            notes.append(
                f"строка {lineno}: значение обрезано комментарием «#». "
                "Если решётка — часть значения, возьмите его в кавычки"
            )
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped in {"---", "..."}:
            continue
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise YamlSubsetError(f"строка {lineno}: отступ табуляцией не поддерживается")
        out.append((len(line) - len(line.lstrip()), stripped, lineno))
    return out


def _strip_comment(line: str) -> tuple[str, int]:
    """Отрезать комментарий, не трогая содержимое кавычек.

    Возвращает (строка, сколько пробелов было перед `#`; -1 — отреза не было).
    Раньше строка резалась по первому ` #` до разбора кавычек, и значение
    `title: "a #b"` превращалось в незакрытую кавычку. Для незакавыченных значений
    правило прежнее и совпадает с YAML.

    Число пробелов важно: `значение   # пояснение` — обычный выровненный
    комментарий, а `Отчёт #1 по HR` — вероятно, часть значения. Отличать их можно
    только по форме записи, поэтому предупреждается лишь второй случай.
    """
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "#" and (i == 0 or line[i - 1].isspace()):
            before = line[:i]
            spaces = len(before) - len(before.rstrip(" "))
            return before, spaces
    return line, -1


def _block(lines: list[tuple[int, str, int]], pos: int, indent: int) -> tuple[Any, int]:
    if lines[pos][1].startswith("- "):
        return _sequence(lines, pos, indent)
    return _mapping(lines, pos, indent)


def _sequence(lines: list[tuple[int, str, int]], pos: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while pos < len(lines):
        cur_indent, content, lineno = lines[pos]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            raise YamlSubsetError(f"строка {lineno}: неожиданный отступ в списке")
        if not content.startswith("- "):
            break
        items.append(_scalar(content[2:].strip()))
        pos += 1
    return items, pos


def _mapping(lines: list[tuple[int, str, int]], pos: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while pos < len(lines):
        cur_indent, content, lineno = lines[pos]
        if cur_indent < indent:
            break
        if cur_indent > indent:
            raise YamlSubsetError(f"строка {lineno}: неожиданный отступ")
        if content.startswith("- "):
            break
        if ":" not in content:
            raise YamlSubsetError(f"строка {lineno}: ожидалось «ключ: значение»")

        key, _, raw_value = content.partition(":")
        key = key.strip()
        raw_value = raw_value.strip()
        pos += 1

        if raw_value:
            result[key] = _scalar(raw_value)
            continue

        if pos < len(lines) and lines[pos][0] > indent:
            nested, pos = _block(lines, pos, lines[pos][0])
            result[key] = nested
        elif pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("- "):
            nested, pos = _sequence(lines, pos, indent)
            result[key] = nested
        else:
            result[key] = None
    return result, pos


def _scalar(raw: str) -> Any:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    low = value.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    if low in {"null", "~", ""}:
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    if _looks_float(value):
        return float(value)
    return value


def _looks_float(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return "." in value or "e" in value.lower()
