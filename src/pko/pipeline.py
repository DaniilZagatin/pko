"""Сборка конвейера: от коммита до готовых отчётов.

Порядок фиксирован и не зависит от языковых моделей:
факты → кандидаты → модель → детерминированная валидация → проверки → решение Gate.
Языковая модель может предложить наблюдение; повлиять на проверку оно может
только после профильной структурной проверки, а SQL — только из статического
AST-экстрактора. Неполный агентский прогон на Gate не влияет.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pko.agent.loop import AgentResult, map_groups_to_candidates, run_scout
from pko.assemble.candidates import Candidate, build_candidates
from pko.assemble.heuristic import build_model
from pko.assemble.llm_map import propose_bbb_groups
from pko.checks.validator import ERROR, Issue, validate
from pko.extractors.base import Fact, Tree
from pko.extractors.runner import Extraction, extract_all
from pko.gate.decide import GateDecision, decide
from pko.gate.evaluate import CheckResult, evaluate_checks
from pko.gate.profile import Profile, determine_profile
from pko.git.repo import GitRepo
from pko.history.selector import Version
from pko.intent.loader import IntentResult, load_intent
from pko.llm.registry import ModelSpec
from pko.model.readiness import Readiness, assess
from pko.model.schema import PkoModel


@dataclass
class VersionAnalysis:
    version: Version
    model: PkoModel
    extraction: Extraction
    candidates: list[Candidate]
    intent: IntentResult
    issues: list[Issue]
    checks: list[CheckResult]
    profile: Profile
    decision: GateDecision
    readiness: Readiness = field(default_factory=lambda: assess("BASIC"))
    notes: list[str] = field(default_factory=list)
    agent: AgentResult | None = None

    @property
    def has_errors(self) -> bool:
        return any(i.level == ERROR for i in self.issues)


def analyze_version(
    repo: GitRepo,
    version: Version,
    repo_name: str,
    branch: str,
    junit_path: str | Path | None = None,
    intent_path: str | Path | None = None,
    assembler: ModelSpec | None = None,
    scout: ModelSpec | None = None,
    agent_max_steps: int = 0,
    agent_packs: list[str] | None = None,
) -> VersionAnalysis:
    """Полный разбор одной версии репозитория.

    При заданном `scout` разведку ведёт агент: он читает репозиторий сам, а его
    находки проверяются кодом. Сборщик (`assembler`) в этом режиме не
    вызывается вовсе — группировку целиком отдаём агенту.
    """
    tree = Tree.at(repo, version.sha)
    extraction = extract_all(tree, junit_path=junit_path)

    meta: dict[str, Any] = {
        "repo": repo_name,
        "branch": branch,
        "commit": version.sha,
        "commit_date": version.commit.date,
        "commit_subject": version.commit.subject,
        "version_label": version.label,
        "version_reason": version.reason,
    }

    agent: AgentResult | None = None
    agent_notes: list[str] = []
    if scout is not None:
        agent = run_scout(
            tree=tree, extraction=extraction, spec=scout, meta=meta,
            max_steps=agent_max_steps, packs=agent_packs,
        )
        agent_notes = list(agent.notes)
        # Находки агента дальше идут общим путём: кандидаты, модель, проверки.
        # Основание смешивать их со статическими — проверка в `verify_facts`:
        # у видов, влияющих на вердикт (`SQL_WRITE`, `GRAPH_NODE`, `GRAPH_EDGE`,
        # `ROUTE`), рядом со ссылкой должна стоять сама конструкция. Это
        # структурная проверка: комментарии, строковые литералы и docstring
        # маскируются. SQL из строки агентом до Gate не повышается — для него
        # остаётся детерминированный AST-экстрактор.
        extraction.facts, duplicates = _merge_facts(extraction.facts, agent.facts)
        if duplicates:
            agent_notes.append(
                f"Агент: {duplicates} находок совпали со статическим разбором "
                "по смыслу и строке и не были учтены повторно"
            )
        if agent.facts:
            agent_notes.append(
                f"Агент: принято фактов из разведки — {len(agent.facts)} "
                f"(каждый со ссылкой на строку кода, проверенной по конструкции)"
            )
        # Наблюдение в незнакомом механизме остаётся в отчёте, но в вердикт не
        # входит. Молчать об этом нельзя: читатель иначе решит, что допуск
        # учитывал всё найденное.
        outside = [f for f in agent.facts if not f.gate_eligible]
        if outside:
            mechanisms = sorted({f.facets.mechanism or "не указан" for f in outside})
            agent_notes.append(
                f"Агент: наблюдений вне вердикта Gate — {len(outside)} "
                f"(механизмы: {', '.join(mechanisms)}); структурной проверки для них нет, "
                f"в решении о допуске они не участвуют"
            )
        if agent.trace.packs:
            agent_notes.append(
                "Агент: подключены паки промпта — " + ", ".join(agent.trace.packs)
            )
        if agent.incomplete:
            agent_notes.append(
                f"Агент не завершил обход ({agent.trace.stop_reason}): "
                "картина системы может быть неполной"
            )

    candidates = build_candidates(extraction)
    intent = load_intent(tree, version.sha, override_path=intent_path)

    if agent is not None:
        groups = map_groups_to_candidates(agent.groups, candidates)
        if not groups:
            agent_notes.append(
                "Агент не предложил ни одной годной группы — блоки собраны по пакетам"
            )
        meta["scout"] = scout.model if scout else ""
        meta["scout_endpoint"] = scout.base_url if scout else ""
        meta["assembler"] = "не используется: разведку ведёт агент"
        meta["grouping_source"] = "agent" if groups else "packages"
        trajectory = agent.process_trajectory
        invariants = agent.guardrail_invariants
    else:
        grouping = propose_bbb_groups(candidates, assembler)
        agent_notes.extend(grouping.notes)
        groups = grouping.groups
        meta["assembler"] = assembler.model if assembler else "не используется"
        meta["grouping_source"] = grouping.source
        trajectory = None
        invariants = None

    model = build_model(
        extraction=extraction,
        candidates=candidates,
        meta=meta,
        intent=intent.data if intent.present else None,
        bbb_groups=groups or None,
        process_trajectory=trajectory,
        guardrail_invariants=invariants,
    )

    # Внешние inputs в evidence уже названы переносимыми ID. Сырой путь флага
    # сюда возвращать нельзя: валидатору он не нужен, а при выводе issue мог бы
    # снова попасть в публикуемый отчёт.
    report_sources = {f.path for f in extraction.by_kind("TEST_REPORT") if f.path}
    external = ({intent.source} if intent.source else set()) | report_sources
    issues = validate(model, known_files=set(tree.files), external_paths=external)
    # Пробелы отчёта пополняются предупреждениями валидатора и заметками сборщика.
    model.gaps.extend(i.render() for i in issues if i.level != ERROR)
    model.gaps.extend(agent_notes)
    if intent.error:
        model.gaps.append(f"business_intent.yaml прочитан с ошибкой: {intent.error}")
    if intent.invalid:
        model.gaps.append(intent.problem())
    model.gaps.extend(f"business_intent.yaml, {w}" for w in intent.warnings)
    if intent.present and intent.missing:
        model.gaps.append(
            "В business_intent.yaml не заполнены поля: " + ", ".join(intent.missing)
        )
    # Незаполненные поля записи допуска §8.0.1 — конкретные задачи владельцу.
    # Общий процент неизвестности на них не отвечает: «34% полей не
    # установлено» починить нельзя, «границу решения не задали» — можно.
    for gap in intent.record_gaps:
        model.gaps.append(f"Запись допуска неполна — {gap}")

    # Профилирование получает данные только из пригодного intent: файл с неизвестным
    # значением перечислимого поля к решению не допускается.
    profile = determine_profile(intent.data if intent.usable else None)
    # Готовность к промышленному контуру считается отдельно от допуска: профиль
    # FULL означает, что требования §6 применяются, а не что они выполнены.
    readiness = assess(profile.value, model.counts())
    checks = evaluate_checks(model, extraction, intent, issues)
    decision = decide(
        results=checks,
        profile=profile,
        requested_mode=str(intent.data.get("requested_mode", "ASSIST")),
        implementation_ref=f"{repo_name}@{version.sha[:12]}",
        # Вердикт выносится только по заполненному входу. Незаполненный шаблон —
        # это не отказ, а отсутствие решения: `NO_DECISION` ничего не разрешает,
        # верхняя граница режима остаётся пустой, код возврата отличен от нуля.
        intent_present=intent.complete,
        draft_reason=intent.problem() or "business_intent.yaml не найден",
    )

    notes = list(extraction.notes) + agent_notes
    return VersionAnalysis(
        readiness=readiness,
        version=version,
        model=model,
        extraction=extraction,
        candidates=candidates,
        intent=intent,
        issues=issues,
        checks=checks,
        profile=profile,
        decision=decision,
        notes=notes,
        agent=agent,
    )


def _merge_facts(static: list[Fact], proposed: list[Fact]) -> tuple[list[Fact], int]:
    """Добавить агентские наблюдения без удвоения тех же мест кода.

    Формулировки `key/value` у AST-экстрактора и модели закономерно различаются,
    поэтому идентичность — это нормализованный смысл плюс точная ссылка. При
    совпадении сохраняется статический факт: у него более сильная provenance и
    он не должен быть заменён эвристикой агента.
    """
    merged = list(static)
    static_locations: dict[tuple[str, str, str, int | None], set[str]] = {}
    for fact in static:
        base, action = _semantic_location(fact)
        static_locations.setdefault(base, set()).add(action)
    duplicates = 0
    for fact in proposed:
        base, action = _semantic_location(fact)
        static_actions = static_locations.get(base, set())
        # `action` у универсального наблюдения необязателен. Если один источник
        # назвал `serve`, а второй оставил действие пустым, при совпадении
        # category/mechanism/path:line это всё равно одно место кода; сохраняем
        # более сильный статический факт.
        if static_actions and (
            action in static_actions or not action or "" in static_actions
        ):
            duplicates += 1
            continue
        merged.append(fact)
    return merged, duplicates


def _semantic_location(
    fact: Fact,
) -> tuple[tuple[str, str, str, int | None], str]:
    facets = fact.facets
    return (facets.category, facets.mechanism, fact.path, fact.line), facets.action
