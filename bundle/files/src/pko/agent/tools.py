"""Инструменты агента поверх снимка коммита.

Четыре операции, все только на чтение и все привязаны к одному `sha`. Агент не
получает bash: рабочего дерева у зеркала нет, а allowlist подкоманд в
`pko.git.repo` — единственное, чем доказывается, что анализ ничего не меняет.

Из выдачи убрано то, что не является прикладным кодом (`is_vendor`) и то, что
нельзя показывать модели: файлы окружения целиком и значения параметров с
именами, похожими на секрет.
"""

from __future__ import annotations

import fnmatch
import multiprocessing
import re
import time
from dataclasses import dataclass, field
from typing import Any

from pko.agent import verifiers
from pko.extractors.base import FACT_KINDS, Tree, is_vendor
from pko.extractors.python_code import SECRET_HINTS
from pko.model import taxonomy

# Файлы окружения не отдаются даже по прямому запросу: там лежат ключи.
SECRET_FILES = ("*.env", ".env", ".env.*", "*.pem", "*.key", "*.keytab", "*_rsa", "*.p12")

MAX_READ_LINES = 400
MAX_SEARCH_HITS = 60
MAX_LIST_FILES = 400

# `ключ = "значение"` и `ключ: "значение"` — обе формы, в которых секрет
# попадается в конфигурации и в коде.
_ASSIGN = re.compile(r"^(?P<head>\s*[\w.\[\]\"']*?(?P<name>[\w]+)\s*[:=]\s*)(?P<value>\S.*)$")

# Секрет чаще встречается не отдельной строкой присваивания, а внутри вызова
# (`Client(api_key="sk-...")`) или словаря заголовков. Такие места первая
# форма не ловит: имя стоит не в начале строки.
_INLINE_SECRET = re.compile(
    r"""(?P<name>["']?[\w.\-]*
            (?:key|token|secret|password|passwd|keytab|credential|authorization|auth)
        [\w.\-]*["']?)
        (?P<sep>\s*[:=]\s*)
        (?P<value>"[^"]*"|'[^']*'|[^\s,;)}\]]+)""",
    re.IGNORECASE | re.VERBOSE,
)

# Готовый заголовок авторизации: имя параметра рядом может и не стоять.
_BEARER = re.compile(r"\b(?P<scheme>Bearer|Basic|Token)\s+(?P<value>[\w.\-+/=]{8,})",
                     re.IGNORECASE)

MASKED = "<скрыто>"

# Квантификатор, применённый к группе, — единственная форма, из которой
# вырастает катастрофический откат: `(a+)+`, `(a|a)+`, `(a|ab)*`. Отличить
# безобидный `(foo.*bar)+` от разрушительного по виду шаблона нельзя, поэтому
# это не запрет, а признак «шаблон небезопасен» — такой поиск уходит в
# отдельный процесс, который можно убить по таймауту. Всё остальное
# выполняется на месте: `re` не прерывается изнутри, но простому шаблону
# прерывание и не нужно.
_GROUP_QUANTIFIER = re.compile(r"(?<!\\)\)\s*[*+{]")

# Верхняя граница работы поиска: и для простого шаблона по большому дереву,
# и для небезопасного, выполняемого отдельным процессом.
SEARCH_SECONDS = 5.0

# Длина строки, которую отдаём движку. Ограничение не спасает от отката само по
# себе, но снимает основную массу работы на машинно-сгенерированных файлах.
MAX_SEARCH_LINE = 2000

# Как часто проверять таймаут внутри файла: пофайловой проверки мало, длинный
# файл успевает съесть весь бюджет.
DEADLINE_EVERY = 500


@dataclass
class ToolResult:
    ok: bool
    content: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolBox:
    """Набор инструментов одного прогона. Считает, что и сколько было прочитано."""

    tree: Tree
    facts: list[dict[str, Any]] = field(default_factory=list)
    bytes_read: int = 0
    files_read: set[str] = field(default_factory=set)
    # Незакрытые «хвосты»: ключ выдачи → offset, с которого её нужно
    # продолжить. Счётчик обрезаний здесь не годится — он монотонен, и
    # добросовестно долистанное до конца дерево навсегда осталось бы
    # помеченным как неполное. Запись исчезает, когда продолжение дошло
    # до конца.
    pending_pages: dict[str, int] = field(default_factory=dict)
    # Выдачи, дочитанные до конца хотя бы раз: повторный запрос первой
    # страницы не должен снова объявлять обход неполным.
    resolved_pages: set[str] = field(default_factory=set)
    # Поиски, снятые по таймауту. Продолжения у них нет, поэтому это
    # отдельный признак неполноты, который не закрывается ничем.
    timed_out_searches: int = 0
    # Номер текущего шага цикла: проставляется снаружи, чтобы факт можно было
    # вернуть к шагу, на котором он был записан.
    current_step: int = 0

    # --- публичные операции ------------------------------------------------
    def list_files(self, glob: str = "*", offset: int = 1) -> ToolResult:
        """Дерево коммита без vendor-каталогов, страницами по `MAX_LIST_FILES`.

        Обрезание без продолжения делало обход неполным молча: агент видел
        первые 400 путей и считал, что это всё дерево. Теперь остаток
        доступен через `offset`, а в ответе сказано, как его запросить.
        """
        visible = [p for p in self.tree.files if not is_vendor(p)]
        matched = sorted(
            p for p in visible
            if fnmatch.fnmatch(p, glob) or fnmatch.fnmatch(p.rsplit("/", 1)[-1], glob)
        )
        start = max(1, offset)
        shown = matched[start - 1: start - 1 + MAX_LIST_FILES]
        rest = len(matched) - (start - 1) - len(shown)
        tail = (
            f"\n… ещё {rest}: повторите с offset={start + len(shown)}"
            if rest > 0 else ""
        )
        self._track_page(f"list:{glob}", start + len(shown), rest)
        return ToolResult(
            ok=True,
            content="\n".join(shown) + tail if shown else "ничего не найдено",
            meta={"matched": len(matched), "shown": len(shown),
                  "from": start, "rest": max(0, rest)},
        )

    def read_file(self, path: str, offset: int = 1, limit: int = MAX_READ_LINES) -> ToolResult:
        """Фрагмент файла с номерами строк; значения секретов маскируются."""
        if _is_secret_file(path):
            return ToolResult(
                ok=False,
                content=f"{path}: файл окружения не выдаётся — там хранятся ключи",
                meta={"blocked": True},
            )
        if is_vendor(path):
            return ToolResult(ok=False, content=f"{path}: не прикладной код", meta={"blocked": True})

        text = self.tree.read(path)
        if text is None:
            return ToolResult(ok=False, content=f"{path}: файл не найден на этом коммите")

        lines = text.splitlines()
        start = max(1, int(offset or 1))
        limit = max(1, min(int(limit or MAX_READ_LINES), MAX_READ_LINES))
        chunk = lines[start - 1: start - 1 + limit]

        rendered = "\n".join(
            f"{start + i}\t{_mask_secrets(line)}" for i, line in enumerate(chunk)
        )
        self.bytes_read += len(rendered.encode("utf-8"))
        self.files_read.add(path)
        tail = (
            f"\n… файл длиннее: строк всего {len(lines)}"
            if start - 1 + limit < len(lines) else ""
        )
        return ToolResult(
            ok=True,
            content=rendered + tail,
            meta={"lines_total": len(lines), "from": start, "to": start + len(chunk) - 1},
        )

    def search(self, pattern: str, glob: str = "*", offset: int = 1) -> ToolResult:
        """Поиск по дереву коммита; результат — путь, строка и сама строка.

        Совпадения тоже отдаются страницами: упереться в потолок и промолчать
        значит выдать выборку за полную картину. Хвост забирается тем же
        `offset`, что и в `list_files`.
        """
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            return ToolResult(ok=False, content=f"некорректное регулярное выражение: {exc}")

        start = max(1, offset)
        # На одно совпадение больше, чем показываем: так видно, есть ли хвост.
        wanted = start - 1 + MAX_SEARCH_HITS + 1
        items = self._searchable(glob)

        found, timed_out = _scan(pattern, items, wanted, SEARCH_SECONDS)
        page = found[start - 1: start - 1 + MAX_SEARCH_HITS]
        rest = max(0, len(found) - (start - 1) - len(page))

        lines = [
            f"{path}:{number}\t{_mask_secrets(line.strip())[:200]}"
            for path, number, line in page
        ]
        key = f"search:{pattern}|{glob}"
        if timed_out:
            self.timed_out_searches += 1
            note = (f"\n… поиск снят по таймауту ({SEARCH_SECONDS:g} с): дерево пройдено "
                    f"не полностью, продолжения нет — упростите шаблон")
        elif rest > 0:
            note = f"\n… ещё {rest}: повторите с offset={start + len(page)}"
        else:
            note = ""
        if not timed_out:
            self._track_page(key, start + len(page), rest)

        return ToolResult(
            ok=True,
            content=("\n".join(lines) if lines else "совпадений нет") + note,
            meta={"hits": len(page), "from": start, "rest": rest,
                  "timed_out": timed_out},
        )

    def _searchable(self, glob: str) -> list[tuple[str, str]]:
        """Файлы под шаблон имени: чтение из git идёт здесь, в родителе."""
        out = []
        for path in self.tree.files:
            if is_vendor(path) or _is_secret_file(path):
                continue
            if not (fnmatch.fnmatch(path, glob) or fnmatch.fnmatch(path.rsplit("/", 1)[-1], glob)):
                continue
            text = self.tree.read(path)
            if text:
                out.append((path, text))
        return out

    def _track_page(self, key: str, next_offset: int, rest: int) -> None:
        """Запомнить незакрытый хвост выдачи или закрыть его."""
        if rest > 0:
            if key not in self.resolved_pages:
                self.pending_pages[key] = next_offset
            return
        self.pending_pages.pop(key, None)
        self.resolved_pages.add(key)

    def note_fact(
        self,
        claim: str,
        path: str,
        line: int | None = None,
        kind: str = "",
        category: str = "",
        action: str = "",
        mechanism: str = "",
    ) -> ToolResult:
        """Записать находку. Проверка ссылки — позже, в `pko.agent.verify`.

        Находку можно описать двумя способами: универсально
        (`category`+`action`+`mechanism`) или прежним видом (`kind`). Второй
        короче на знакомом стеке, первый нужен там, где подходящего вида нет:
        обработчик UI-события, команда CLI, запись в файл.
        """
        if kind and kind not in FACT_KINDS:
            return ToolResult(
                ok=False,
                content=f"неизвестный kind «{kind}»; допустимы: {', '.join(FACT_KINDS)}",
            )
        # То же правило, что и в `pko.agent.verify`: расхождение двух путей
        # приёма означало бы «принято к проверке» на то, что проверка не берёт.
        problem = taxonomy.proposal_problem(kind, category, action, mechanism)
        if problem:
            return ToolResult(ok=False, content=problem)
        normalized_category = taxonomy.normalize_category(category)
        if not claim or not path:
            return ToolResult(ok=False, content="факт обязан иметь claim и path")

        facets = taxonomy.Facets(
            category=normalized_category,
            action=taxonomy.normalize_action(action),
            mechanism=taxonomy.normalize_mechanism(mechanism),
        )
        self.facts.append({
            "kind": kind, "claim": claim, "path": path, "line": line,
            "category": facets.category, "action": facets.action,
            "mechanism": facets.mechanism, "step": self.current_step,
        })
        label = kind or f"{facets.category}/{facets.action or '—'}/{facets.mechanism or '—'}"
        # Ответ инструмента говорит то же, что потом решит проверка. Спрашивать
        # `is_covered` здесь было бы мягче правды: у SQL шаблон есть, но в
        # вердикт такое наблюдение всё равно не идёт, и агент узнавал бы об
        # этом только из отчёта.
        base = taxonomy.facets_for(kind)
        resolved = taxonomy.Facets(
            facets.category or base.category,
            facets.action or base.action,
            facets.mechanism or base.mechanism,
        )
        tail = ""
        if not verifiers.is_gate_eligible(resolved):
            tail = " (в вердикт Gate это наблюдение не войдёт)"
        return ToolResult(ok=True, content=f"принято к проверке: {label} {path}:{line}{tail}")

    # --- диспетчер ---------------------------------------------------------
    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        handlers = {
            "list_files": lambda a: self.list_files(
                str(a.get("glob", "*")), int(a.get("offset", 1) or 1),
            ),
            "read_file": lambda a: self.read_file(
                str(a.get("path", "")), int(a.get("offset", 1) or 1),
                int(a.get("limit", MAX_READ_LINES) or MAX_READ_LINES),
            ),
            "search": lambda a: self.search(
                str(a.get("pattern", "")), str(a.get("glob", "*")),
                int(a.get("offset", 1) or 1),
            ),
            "note_fact": lambda a: self.note_fact(
                claim=str(a.get("claim", "")), path=str(a.get("path", "")),
                line=a.get("line"), kind=str(a.get("kind", "")),
                category=str(a.get("category", "")), action=str(a.get("action", "")),
                mechanism=str(a.get("mechanism", "")),
            ),
        }
        handler = handlers.get(name)
        if handler is None:
            return ToolResult(
                ok=False,
                content=f"неизвестный инструмент «{name}»; доступны: {', '.join(handlers)}",
            )
        try:
            return handler(args or {})
        except (TypeError, ValueError) as exc:
            return ToolResult(ok=False, content=f"неверные аргументы: {exc}")


def _scan(
    pattern: str, items: list[tuple[str, str]], wanted: int, seconds: float
) -> tuple[list[tuple[str, int, str]], bool]:
    """Найти до `wanted` совпадений, уложившись в `seconds`.

    Шаблон приходит от модели, и остановить `re` изнутри нельзя: один
    `regex.search` на неудачной строке способен считать часами, и никакая
    проверка времени в цикле до него не доберётся. Поэтому шаблон, к группе
    которого применён квантификатор, выполняется отдельным процессом — его
    можно убить. Остальные идут на месте: там проверки времени по ходу цикла
    достаточно, а лишний процесс на каждый поиск обошёлся бы дороже.
    """
    if _GROUP_QUANTIFIER.search(pattern):
        return _scan_isolated(pattern, items, wanted, seconds)
    return _scan_inline(pattern, items, wanted, time.monotonic() + seconds)


def _scan_inline(
    pattern: str, items: list[tuple[str, str]], wanted: int, deadline: float
) -> tuple[list[tuple[str, int, str]], bool]:
    regex = re.compile(pattern, re.IGNORECASE)
    hits: list[tuple[str, int, str]] = []
    checked = 0
    for path, text in items:
        for number, line in enumerate(text.splitlines(), start=1):
            if regex.search(line[:MAX_SEARCH_LINE]):
                hits.append((path, number, line))
                if len(hits) >= wanted:
                    return hits, False
            checked += 1
            if checked % DEADLINE_EVERY == 0 and time.monotonic() > deadline:
                return hits, True
    return hits, False


def _scan_worker(pattern: str, items: list[tuple[str, str]], wanted: int, conn) -> None:
    """Тело отдельного процесса: то же сканирование, но его можно прервать."""
    try:
        hits, _ = _scan_inline(pattern, items, wanted, float("inf"))
        conn.send(hits)
    except Exception:                                    # noqa: BLE001 — процесс одноразовый
        conn.send([])
    finally:
        conn.close()


def _scan_isolated(
    pattern: str, items: list[tuple[str, str]], wanted: int, seconds: float
) -> tuple[list[tuple[str, int, str]], bool]:
    ctx = _mp_context()
    receiver, sender = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_scan_worker, args=(pattern, items, wanted, sender), daemon=True)
    proc.start()
    sender.close()
    hits: list[tuple[str, int, str]] = []
    timed_out = True
    try:
        if receiver.poll(seconds):
            try:
                hits = receiver.recv()
                timed_out = False
            except EOFError:                             # процесс умер, не ответив
                timed_out = False
    finally:
        receiver.close()
        proc.terminate()
        proc.join(1)
    return hits, timed_out


def _mp_context():
    """`fork` дешевле: копия процесса не переимпортирует пакет на каждый поиск."""
    try:
        return multiprocessing.get_context("fork")
    except ValueError:
        return multiprocessing.get_context("spawn")


def _is_secret_file(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(base, pattern) for pattern in SECRET_FILES)


def _mask_secrets(line: str) -> str:
    """Скрыть значения, похожие на секреты, где бы в строке они ни стояли."""
    match = _ASSIGN.match(line)
    if match and SECRET_HINTS.search(match.group("name")):
        return match.group("head") + MASKED
    masked = _INLINE_SECRET.sub(lambda m: m.group("name") + m.group("sep") + MASKED, line)
    return _BEARER.sub(lambda m: f"{m.group('scheme')} {MASKED}", masked)
