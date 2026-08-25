"""Общий пайплайн прогресса: PPTX-план + целевой репозиторий → ProgressModel.

Вызывается и CLI (`cli.cmd_progress`), и веб-эндпоинтом (`web.app`) — одна
реализация, чтобы поведение двух интерфейсов не могло разойтись. Функция
ничего не печатает и не пишет на диск — это дело вызывающей стороны (CLI
пишет отчёт через `output.publisher`, веб отдаёт HTML прямо в ответе).
"""

from __future__ import annotations

import time
from pathlib import Path

from pko.errors import PkoError
from pko.llm.client import ChatClient
from pko.llm.registry import ModelSpec
from pko.progress.matcher import find_unclaimed_paths, match_plan
from pko.progress.schema import ProgressModel
from pko.progress.target_repo import TargetRepo


def run_progress(
    plan_path: Path,
    repo_name: str,
    target: TargetRepo,
    planner: ModelSpec,
    matcher_spec: ModelSpec,
    planner_client: ChatClient | None = None,
    matcher_client: ChatClient | None = None,
) -> ProgressModel:
    """Собрать ProgressModel: PPTX → пункты плана → сопоставление с кодом.

    `python-pptx` — единственная тяжёлая зависимость на этом пути, поэтому
    импорт `pptx_reader`/`plan_extract` остаётся отложенным здесь, а не
    только в CLI: веб-эндпоинт тоже не должен требовать пакет только ради
    импорта модуля.

    `planner_client`/`matcher_client` — та же инъекция для тестов, что и в
    `extract_plan`/`match_plan`: без нёе `ChatClient` по умолчанию читает и
    пишет реальный `~/.pko/llm-cache`.
    """
    try:
        from pko.progress.pptx_reader import read_deck
    except ImportError as exc:
        raise PkoError(
            "Не установлен python-pptx.",
            hint="поставьте пакет: pip install python-pptx>=1.0",
        ) from exc
    from pko.progress.plan_extract import extract_plan

    slides = read_deck(plan_path)
    plan_result = extract_plan(slides, planner, client=planner_client)
    if not plan_result.usable:
        raise PkoError(
            "Не удалось извлечь пункты плана из презентации.",
            hint="проверьте текст слайдов; причина: " + ("; ".join(plan_result.notes) or "неизвестна"),
        )

    match_result = match_plan(
        plan_result.items, target.extraction, target.tree, matcher_spec, client=matcher_client
    )

    unclaimed = find_unclaimed_paths(target.extraction, match_result.verdicts)
    generated_at = time.strftime("%Y-%m-%d %H:%M")
    return ProgressModel(
        meta={
            "repo": repo_name, "branch": target.branch, "commit": target.sha,
            "plan_source": plan_path.name, "generated_at": generated_at,
        },
        items={item.id: item for item in plan_result.items},
        verdicts=match_result.verdicts,
        unclaimed=unclaimed,
        gaps=plan_result.notes + match_result.notes,
    )
