"""Сборка конвейера: от коммита до готовых отчётов.

Порядок фиксирован и не зависит от языковых моделей:
факты → кандидаты → модель → детерминированная валидация → проверки → решение Gate.
Языковая модель может улучшить группировку блоков и текст, но не может изменить
ни один статус проверки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pko.assemble.candidates import Candidate, build_candidates
from pko.assemble.heuristic import build_model
from pko.assemble.llm_map import propose_bbb_groups
from pko.checks.validator import ERROR, Issue, validate
from pko.extractors.base import Tree
from pko.extractors.runner import Extraction, extract_all
from pko.gate.decide import GateDecision, decide
from pko.gate.evaluate import CheckResult, evaluate_checks
from pko.gate.profile import Profile, determine_profile
from pko.git.repo import GitRepo
from pko.history.selector import Version
from pko.intent.loader import IntentResult, load_intent
from pko.llm.registry import ModelSpec
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
    notes: list[str] = field(default_factory=list)

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
) -> VersionAnalysis:
    """Полный разбор одной версии репозитория."""
    tree = Tree.at(repo, version.sha)
    extraction = extract_all(tree, junit_path=junit_path)
    candidates = build_candidates(extraction)

    intent = load_intent(tree, version.sha, override_path=intent_path)
    grouping = propose_bbb_groups(candidates, assembler)

    meta: dict[str, Any] = {
        "repo": repo_name,
        "branch": branch,
        "commit": version.sha,
        "commit_date": version.commit.date,
        "commit_subject": version.commit.subject,
        "version_label": version.label,
        "version_reason": version.reason,
        "assembler": assembler.model if assembler else "не используется",
        "grouping_source": grouping.source,
    }

    model = build_model(
        extraction=extraction,
        candidates=candidates,
        meta=meta,
        intent=intent.data if intent.present else None,
        bbb_groups=grouping.groups or None,
    )

    external = {p for p in (intent.source, str(junit_path) if junit_path else "") if p}
    issues = validate(model, known_files=set(tree.files), external_paths=external)
    # Пробелы отчёта пополняются предупреждениями валидатора и заметками сборщика.
    model.gaps.extend(i.render() for i in issues if i.level != ERROR)
    model.gaps.extend(grouping.notes)
    if intent.error:
        model.gaps.append(f"business_intent.yaml прочитан с ошибкой: {intent.error}")
    if intent.invalid:
        model.gaps.append(intent.problem())
    model.gaps.extend(f"business_intent.yaml, {w}" for w in intent.warnings)
    if intent.present and intent.missing:
        model.gaps.append(
            "В business_intent.yaml не заполнены поля: " + ", ".join(intent.missing)
        )

    # Профилирование получает данные только из пригодного intent: файл с неизвестным
    # значением перечислимого поля к решению не допускается.
    profile = determine_profile(intent.data if intent.usable else None)
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

    notes = list(extraction.notes) + list(grouping.notes)
    return VersionAnalysis(
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
    )
