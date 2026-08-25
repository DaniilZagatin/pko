"""Цикл разведки.

Протокол текстовый: модель отвечает одним JSON — либо вызовом инструмента, либо
финалом. Нативный tools API не используется намеренно, про его поддержку
внутренними endpoint'ами гарантий нет, а разбор JSON из ответа уже отработан в
`pko.assemble.llm_map`.

Шаги не ограничены по умолчанию: сначала нужно понять качество, оптимизировать
будем после. Единственный автоматический стоп — три подряд идентичных вызова
инструмента; он же помечает прогон неполным.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pko.agent.stack import StackProfile, detect
from pko.agent.tools import ToolBox, ToolResult
from pko.agent.trace import Trace, TraceStep
from pko.agent.verify import verify_facts, verify_groups, verify_invariants
from pko.assemble.candidates import Candidate
from pko.errors import LlmError, PkoError
from pko.extractors.base import Fact, Tree
from pko.extractors.runner import Extraction
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.model import taxonomy

PROMPT_DIR = Path(__file__).with_name("prompts")
PROMPT_PATH = PROMPT_DIR / "scout_core.md"
PACKS_DIR = PROMPT_DIR / "packs"

# Столько одинаковых вызовов подряд считаем зацикливанием.
REPEAT_LIMIT = 3
# Столько раз подряд прощаем невалидный JSON, потом заканчиваем принудительно.
PARSE_ERROR_LIMIT = 3

# Сколько последних сообщений диалога уходит модели помимо системного промпта и
# подсказок статического разбора.
#
# Без обрезки история растёт на два сообщения за шаг и никогда не сокращается: в
# каждом запросе едет весь прочитанный ранее код. При решении «шаги не
# ограничены» это даёт квадратичный рост стоимости и рано или поздно отказ
# endpoint'а по длине контекста — прогон умрёт на середине вместо того, чтобы
# закончиться финалом. Найденные факты при обрезке не теряются: они уже записаны
# `note_fact` в `ToolBox`.
HISTORY_WINDOW = 12

# Журнал обхода едет в каждом запросе, поэтому у него свой бюджет: длина
# записи, длина значения аргумента внутри неё и потолок всего журнала. Без
# потолка журнал растёт линейно по шагам, а переданный объём — квадратично.
JOURNAL_VALUE = 60
JOURNAL_ENTRY = 160
JOURNAL_BUDGET = 3000

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")
_VERSION = re.compile(r"^version:\s*(\S+)", re.MULTILINE)


@dataclass
class AgentResult:
    """Итог разведки: что принято, что предложено и как это получилось."""

    facts: list[Fact] = field(default_factory=list)
    groups: dict[str, list[str]] = field(default_factory=dict)
    process_trajectory: list[str] = field(default_factory=list)
    guardrail_invariants: list[dict[str, Any]] = field(default_factory=list)
    trace: Trace = field(default_factory=Trace)
    notes: list[str] = field(default_factory=list)
    stack: StackProfile = field(default_factory=StackProfile)

    @property
    def incomplete(self) -> bool:
        return self.trace.incomplete


def available_packs() -> list[str]:
    """Паки, лежащие рядом с ядром."""
    return sorted(p.stem for p in PACKS_DIR.glob("*.md"))


def load_prompt(
    path: Path | None = None, packs: list[str] | None = None
) -> tuple[str, str, str]:
    """Собрать промпт из ядра и паков.

    Возвращает (текст, версия ядра, sha собранного текста). Sha считается по
    итоговому тексту, а не по одному ядру: прогоны с разным набором паков —
    это разные условия, и сравнивать их как одинаковые нельзя.
    """
    target = Path(path or PROMPT_PATH)
    text = target.read_text(encoding="utf-8")
    match = _VERSION.search(text)
    version = match.group(1) if match else "0"

    parts = [text]
    for name in packs or []:
        pack = PACKS_DIR / f"{name}.md"
        if not pack.is_file():
            # Молча пропустить значит записать в трассу условия, которых не
            # было: `packs` называл бы пак активным, а в промпт он не попал.
            raise PkoError(
                f"неизвестный пак промпта: {name}",
                hint="доступны: " + ", ".join(available_packs()),
            )
        parts.append(pack.read_text(encoding="utf-8"))
    composed = "\n\n".join(parts)
    sha = hashlib.sha256(composed.encode("utf-8")).hexdigest()[:12]
    return composed, version, sha


def run_scout(
    tree: Tree,
    extraction: Extraction,
    spec: ModelSpec,
    meta: dict[str, Any] | None = None,
    max_steps: int = 0,
    prompt_path: Path | None = None,
    client: ChatClient | None = None,
    packs: list[str] | None = None,
) -> AgentResult:
    """Провести разведку. `max_steps=0` — без ограничения числа шагов."""
    profile = detect(tree, extraction)
    chosen = list(packs) if packs is not None else profile.packs
    prompt, version, prompt_sha = load_prompt(prompt_path, chosen)
    meta = meta or {}
    tools = ToolBox(tree=tree)
    chat = client or ChatClient(spec=spec)

    trace = Trace(
        repo=str(meta.get("repo", "")),
        commit=str(meta.get("commit", "")),
        version_label=str(meta.get("version_label", "")),
        endpoint=spec.base_url,
        model=spec.model,
        prompt_version=version,
        prompt_sha=prompt_sha,
        packs=chosen,
        stack=profile.to_dict(),
    )
    result = AgentResult(trace=trace, stack=profile)
    note = profile.coverage_note()
    if note:
        result.notes.append(note)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": _seed_message(tree, extraction)},
    ]

    recent: list[str] = []
    journal: list[str] = []
    parse_errors = 0
    final: dict[str, Any] | None = None
    step_number = 0
    explored = False

    while True:
        if max_steps and step_number >= max_steps:
            trace.stop_reason = f"исчерпан лимит шагов ({max_steps})"
            trace.incomplete = True
            break

        step_number += 1
        started = time.perf_counter()
        sent = _window(messages, journal)
        try:
            answer = chat.chat(sent)
        except LlmError as exc:
            trace.add(TraceStep(
                number=step_number, request_messages=len(sent), raw_response="",
                action="error", ok=False, seconds=time.perf_counter() - started,
                note=exc.message, request=_manifest(sent),
            ))
            trace.stop_reason = f"ошибка обращения к модели: {exc.message}"
            trace.incomplete = True
            break

        elapsed = time.perf_counter() - started
        parsed = _parse(answer.text)

        if parsed is None:
            parse_errors += 1
            trace.add(TraceStep(
                number=step_number, request_messages=len(sent), raw_response=answer.text,
                action="parse_error", ok=False, seconds=elapsed, usage=answer.usage,
                from_cache=answer.from_cache, request=_manifest(sent),
                note=f"ответ не является JSON (подряд: {parse_errors})",
            ))
            if parse_errors >= PARSE_ERROR_LIMIT:
                trace.stop_reason = "модель подряд отвечает не JSON"
                trace.incomplete = True
                break
            messages.append({"role": "assistant", "content": answer.text})
            messages.append({
                "role": "user",
                "content": "Ответ должен быть одним JSON-объектом: вызов инструмента или final.",
            })
            continue

        parse_errors = 0

        if "final" in parsed:
            raw_final = parsed.get("final")
            # Модель вправе прислать `{"final": "done"}` — это не объект, и обращаться
            # к нему как к словарю нельзя: прогон упал бы вместо того, чтобы честно
            # закончиться пустым финалом.
            final = raw_final if isinstance(raw_final, dict) else {}
            malformed = not isinstance(raw_final, dict)
            trace.add(TraceStep(
                number=step_number, request_messages=len(sent), raw_response=answer.text,
                action="final", seconds=elapsed, usage=answer.usage,
                from_cache=answer.from_cache, request=_manifest(sent),
                ok=not malformed,
                note="финал не является объектом: находки не переданы" if malformed else "",
            ))
            trace.stop_reason = (
                "агент прислал финал неверной формы" if malformed
                else "агент завершил разведку"
            )
            trace.incomplete = malformed
            break

        tool = str(parsed.get("tool", "")).strip()
        args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
        signature = _signature(tool, args)

        # Держим ровно столько подписей, сколько нужно для проверки повтора:
        # при неограниченных шагах список иначе растёт весь прогон.
        recent.append(signature)
        del recent[:-REPEAT_LIMIT]
        if len(recent) >= REPEAT_LIMIT and len(set(recent)) == 1:
            trace.add(TraceStep(
                number=step_number, request_messages=len(sent), raw_response=answer.text,
                action="tool", tool=tool, args=args, ok=False, seconds=elapsed,
                usage=answer.usage, from_cache=answer.from_cache, request=_manifest(sent),
                note=f"вызов повторён {REPEAT_LIMIT} раза подряд",
            ))
            trace.stop_reason = f"повтор одного вызова {REPEAT_LIMIT} раза подряд"
            trace.incomplete = True
            break

        tools.current_step = step_number
        if tool == "static_hints":
            if not explored:
                outcome = ToolResult(
                    ok=False,
                    content="Сначала самостоятельно осмотрите дерево через list_files/read_file/search.",
                )
            else:
                outcome = ToolResult(ok=True, content=_static_hints(extraction))
        else:
            outcome = tools.call(tool, args)
            if outcome.ok and tool in {"list_files", "read_file", "search"}:
                explored = True
        trace.add(TraceStep(
            number=step_number, request_messages=len(sent), raw_response=answer.text,
            action="tool", tool=tool, args=args, result=outcome.content, ok=outcome.ok,
            seconds=elapsed, usage=answer.usage, from_cache=answer.from_cache,
            request=_manifest(sent),
        ))
        journal.append(_journal_line(step_number, tool, args, outcome))

        messages.append({"role": "assistant", "content": answer.text})
        messages.append({"role": "user", "content": outcome.content})

    _collect(result, final, tools, tree, step_number)
    trace.bytes_read = tools.bytes_read
    trace.files_read = len(tools.files_read)
    # Неполнота — это незакрытый хвост выдачи, а не сам факт разбиения на
    # страницы: агент, дочитавший дерево до конца, обошёл его полностью.
    if tools.pending_pages:
        trace.incomplete = True
        result.notes.append(
            f"Агент: выдач с недочитанным остатком — {len(tools.pending_pages)} "
            f"({', '.join(sorted(tools.pending_pages))}); обход неполный"
        )
    if tools.timed_out_searches:
        trace.incomplete = True
        result.notes.append(
            f"Агент: поисков снято по таймауту — {tools.timed_out_searches}; "
            f"продолжения у них нет, обход неполный"
        )

    # Частичный обход может содержать верные локальные находки, поэтому они
    # остаются в паспорте и trace. Но до успешного финала агент не доказал
    # полноту картины: положительные свидетельства такого прогона не должны
    # улучшать или ухудшать решение Gate.
    if trace.incomplete and any(f.gate_eligible for f in result.facts):
        result.facts = [replace(f, gate_eligible=False) for f in result.facts]
        for row in trace.accepted_facts:
            row["gate_eligible"] = False
            row["reason"] = str(row.get("reason", "")) + (
                "; прогон неполный — наблюдение исключено из Gate"
            )
        result.notes.append(
            "Агент завершился неполно: все его наблюдения исключены из вердикта Gate"
        )
    return result


# Сколько начала сообщения хранить в описи запроса: хватает, чтобы узнать
# сообщение и понять, тот ли файл в нём приехал.
MANIFEST_PREVIEW = 200


def _manifest(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Опись отправленных сообщений: роль, размер, начало."""
    out = []
    for message in messages:
        content = str(message.get("content", ""))
        out.append({
            "role": message.get("role", ""),
            "chars": len(content),
            "preview": content[:MANIFEST_PREVIEW],
        })
    return out


def _traced(verdict) -> dict[str, Any]:
    """Предложение плюс то, чем оно оказалось после приведения признаков."""
    row = {**verdict.proposal, "reason": verdict.reason}
    if verdict.fact is not None:
        facets = verdict.fact.facets
        row.update(category=facets.category, action=facets.action,
                   mechanism=facets.mechanism, gate_eligible=verdict.fact.gate_eligible)
    return row


def _attach_verdicts(trace, verdicts, final_step: int) -> None:
    """Приписать вердикты к шагам и снять «успех» с шага с отброшенным фактом.

    Факт из `note_fact` знает свой шаг; факты, перечисленные только в финале,
    относятся к финальному шагу.
    """
    by_step: dict[int, list[dict[str, Any]]] = {}
    for verdict in verdicts:
        try:
            number = int(verdict.proposal.get("step") or 0)
        except (TypeError, ValueError):
            number = 0
        if number < 1:
            number = final_step
        facets = verdict.fact.facets if verdict.fact is not None else None
        by_step.setdefault(number, []).append({
            "ok": verdict.ok,
            "kind": verdict.proposal.get("kind", ""),
            "category": facets.category if facets else verdict.proposal.get("category", ""),
            "action": facets.action if facets else verdict.proposal.get("action", ""),
            "mechanism": facets.mechanism if facets else verdict.proposal.get("mechanism", ""),
            "path": verdict.proposal.get("path", ""),
            "line": verdict.proposal.get("line"),
            "reason": verdict.reason,
        })
    for step in trace.steps:
        items = by_step.get(step.number)
        if not items:
            continue
        step.verdicts = items
        rejected = [i for i in items if not i["ok"]]
        if rejected:
            step.ok = False
            reasons = "; ".join(i["reason"] for i in rejected[:3])
            step.note = (step.note + " " if step.note else "") + (
                f"проверка отклонила фактов: {len(rejected)} — {reasons}"
            )


def _journal_line(number: int, tool: str, args: dict[str, Any], outcome) -> str:
    """Одна строка о шаге: без кода, только что вызвали и что получилось."""
    shown = ", ".join(
        f"{k}={str(v)[:JOURNAL_VALUE]}" for k, v in sorted(args.items()) if k != "claim"
    )
    if tool == "note_fact":
        summary = f"факт {args.get('kind', '')} {args.get('path', '')}:{args.get('line')}"
    elif outcome.ok:
        summary = ", ".join(f"{k}={v}" for k, v in sorted(outcome.meta.items())) or "готово"
    else:
        summary = f"ошибка: {outcome.content[:80]}"
    return f"#{number} {tool}({shown}) → {summary}"[:JOURNAL_ENTRY]


def _compact(journal: list[str]) -> list[str]:
    """Уложить журнал в бюджет символов, свернув старое в одну строку.

    Журнал едет в каждом запросе, поэтому его длина — это не «плюс строка на
    шаг», а плюс строка на шаг в каждом из оставшихся шагов. При неограниченном
    числе шагов объём переданного рос бы квадратично, и endpoint рано или
    поздно отказал бы. Свежие шаги важнее старых: старые сворачиваются в счёт
    по инструментам.
    """
    tail: list[str] = []
    budget = JOURNAL_BUDGET
    for line in reversed(journal):
        if budget - len(line) - 1 < 0:
            break
        tail.append(line)
        budget -= len(line) + 1
    tail.reverse()

    dropped = journal[: len(journal) - len(tail)]
    if not dropped:
        return tail
    counts: dict[str, int] = {}
    for line in dropped:
        name = line.split(" ", 1)[-1].split("(", 1)[0]
        counts[name] = counts.get(name, 0) + 1
    summary = ", ".join(f"{name} ×{n}" for name, n in sorted(counts.items()))
    return [f"#1–#{len(dropped)} свёрнуто ({len(dropped)} шагов): {summary}"] + tail


def map_groups_to_candidates(
    groups: dict[str, list[str]], candidates: list[Candidate]
) -> dict[str, list[str]]:
    """Перевести группы «имя → пути» в «имя → id кандидатов».

    `build_model` ожидает идентификаторы кандидатов, а агент рассуждает путями:
    ему проще сказать «блок собран из `src/api`», чем перечислять `route:*`.
    """
    out: dict[str, list[str]] = {}
    for name, paths in groups.items():
        members: list[str] = []
        for cand in candidates:
            if cand.type != "CAPABILITY":
                continue
            if any(_belongs(cand, path) for path in paths):
                members.append(cand.id)
        if members:
            out[name] = members
    return out


def _belongs(candidate: Candidate, path: str) -> bool:
    """Относится ли кандидат к указанному агентом пути.

    Агент называет и каталоги, и отдельные файлы. Для файла недостаточно
    сравнить с расположением кандидата: модуль-кандидат представлен первым
    файлом пакета, и `src/pko/cli.py` не совпадёт с `src/pko/__init__.py`.
    Поэтому модуль считается принадлежащим пути, если путь лежит внутри пакета.
    """
    location = candidate.path or candidate.group
    prefix = path.rstrip("/")
    if location == path or location.startswith(prefix + "/"):
        return True
    if candidate.subtype == "MODULE":
        package = candidate.group.rstrip("/")
        return bool(package) and (path == package or path.startswith(package + "/"))
    return False


# --- внутреннее ------------------------------------------------------------
def _collect(
    result: AgentResult, final: dict[str, Any] | None, tools: ToolBox, tree: Tree,
    final_step: int = 0,
) -> None:
    """Слить находки инструментов и финала, проверив каждую по коду."""
    proposals = list(tools.facts)
    if final:
        # Из финала берём только известные поля. Остальное — произвольный ввод
        # модели, и служебный `step` из него дальше пошёл бы в `int()`.
        proposals.extend(
            {"kind": p.get("kind", ""), "claim": p.get("claim"),
             "path": p.get("path"), "line": p.get("line"),
             "category": p.get("category", ""), "action": p.get("action", ""),
             "mechanism": p.get("mechanism", "")}
            for p in (final.get("facts") or []) if isinstance(p, dict)
        )
    # Промпт предлагает оба способа: записывать находку сразу через `note_fact` и
    # перечислить её же в финале. Агент естественно делает и то, и другое, и без
    # склейки один факт дважды прошёл бы проверку, удвоив счётчики и породив
    # парные атомарные операции.
    proposals = _dedupe(proposals)

    facts, verdicts = verify_facts(proposals, tree)
    result.facts = facts
    # В трассу кладём и приведённые фасеты: сырое предложение могло прийти без
    # них (прежний вид) или в чужом написании, а читателю трассы нужно видеть
    # то же, чем оперировала проверка.
    result.trace.accepted_facts = [_traced(v) for v in verdicts if v.ok]
    result.trace.rejected_facts = [_traced(v) for v in verdicts if not v.ok]
    _attach_verdicts(result.trace, verdicts, final_step)
    if result.trace.rejected_facts:
        result.notes.append(
            f"Агент: отброшено фактов без подтверждения ссылкой — "
            f"{len(result.trace.rejected_facts)}"
        )

    if not final:
        result.notes.append("Агент не дошёл до финала: группировка и траектория не предложены")
        return

    groups, group_problems = verify_groups(
        [g for g in (final.get("groups") or []) if isinstance(g, dict)], tree
    )
    result.groups = groups
    result.notes.extend(f"Агент: {p}" for p in group_problems)

    trajectory = [str(s).strip() for s in (final.get("process_trajectory") or []) if str(s).strip()]
    result.process_trajectory = trajectory

    invariants, invariant_problems = verify_invariants(
        [i for i in (final.get("guardrail_invariants") or []) if isinstance(i, dict)], tree
    )
    result.guardrail_invariants = invariants
    result.notes.extend(f"Агент: {p}" for p in invariant_problems)


def _window(
    messages: list[dict[str, Any]], journal: list[str] | None = None
) -> list[dict[str, Any]]:
    """Системный промпт и подсказки, журнал обхода, затем последние обмены.

    Начало диалога держим всегда: в нём задача независимой инвентаризации.
    Хвост режем, иначе каждый следующий запрос везёт весь ранее прочитанный код.

    Обрезка выбрасывала первые чтения целиком, и агент к середине прогона не
    помнил, что уже смотрел: мог заново обходить одно и то же и при этом
    считать обход завершённым. Журнал — одна строка на шаг: что вызвали, что
    получили. Кода в нём нет, поэтому он остаётся дешёвым весь прогон.
    """
    if len(messages) <= HISTORY_WINDOW + 2:
        return messages
    head = messages[:2]
    if journal:
        journal = _compact(journal)
        head = head + [{
            "role": "user",
            "content": "Журнал уже сделанных шагов (полные результаты вытеснены из "
                       "истории, повторно их не запрашивайте без нужды):\n"
                       + "\n".join(journal),
        }]
    return head + messages[-HISTORY_WINDOW:]


def _dedupe(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Убрать повторы, сохранив порядок первого появления."""
    seen: set[tuple[str, ...]] = set()
    out: list[dict[str, Any]] = []
    for item in proposals:
        # `line` приходит от модели и может оказаться списком или словарём:
        # в ключ множества такое не положить, поэтому приводим к строке.
        # В ключ входят и фасеты: у универсальных наблюдений `kind` пуст, и
        # без них чтение и запись в одной строке слиплись бы в одно.
        #
        # Приводим их здесь, а не полагаемся на источник: `note_fact` кладёт
        # уже нормализованные значения, а финал модели копируется дословно.
        # Тогда одна и та же находка, названная `SQL` и `sql`, давала два
        # разных ключа, проходила проверку дважды и удваивала «Количество
        # мест вызова» в паспорте — ровно то, ради чего дедупликация и есть.
        key = (
            str(item.get("kind", "")),
            taxonomy.normalize_category(item.get("category", "")),
            taxonomy.normalize_action(item.get("action", "")),
            taxonomy.normalize_mechanism(item.get("mechanism", "")),
            str(item.get("path", "")),
            str(item.get("line")),
            str(item.get("claim", "")).strip().lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _seed_message(tree: Tree, extraction: Extraction) -> str:
    """Первое сообщение без статических находок: защита от anchoring."""
    return (
        f"В коммите {len(tree.files)} файлов. Сначала самостоятельно осмотрите дерево, "
        "точки входа, траекторию, эффекты и контроли. Когда независимая картина "
        "сложится, вызовите static_hints без аргументов и сравните её со статическим разбором."
    )


def _static_hints(extraction: Extraction) -> str:
    """Подсказки экстракторов, доступные только после начала самостоятельного обхода."""
    by_kind: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    for fact in extraction.facts:
        by_kind[fact.kind] = by_kind.get(fact.kind, 0) + 1
        if len(samples.setdefault(fact.kind, [])) < 3:
            samples[fact.kind].append(f"{fact.key} ({fact.path}:{fact.line})")

    digest = {
        "files_total": extraction.coverage.files_total,
        "coverage": {
            "analyzed": extraction.coverage.files_analyzed,
            "total": extraction.coverage.files_total,
        },
        "facts_by_kind": by_kind,
        "samples": samples,
    }
    return (
        "Статический разбор нашёл следующее. Он знает ограниченный набор фреймворков "
        "и мог не увидеть точки входа, если стек ему незнаком. Проверь расхождения.\n\n"
        + json.dumps(digest, ensure_ascii=False, indent=2)
    )


def _parse(raw: str) -> dict[str, Any] | None:
    match = _JSON_BLOCK.search(raw or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _signature(tool: str, args: dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "args": args}, ensure_ascii=False, sort_keys=True)
