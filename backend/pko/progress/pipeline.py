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
from pko.progress.matcher import DEFAULT_MAX_STEPS, find_unclaimed_paths, run_agent
from pko.progress.schema import ProgressModel
from pko.progress.summarize import summarize_progress
from pko.progress.target_repo import TargetRepo


def run_progress(
    plan_path: Path,
    repo_name: str,
    target: TargetRepo,
    spec: ModelSpec,
    client: ChatClient | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
    reporter: ModelSpec | None = None,
    reporter_client: ChatClient | None = None,
) -> ProgressModel:
    """Собрать ProgressModel: PPTX + репозиторий → единый агент → сводный вывод.

    `python-pptx` — единственная тяжёлая зависимость на этом пути, поэтому
    импорт `pptx_reader` остаётся отложенным здесь, а не только в CLI:
    веб-эндпоинт тоже не должен требовать пакет только ради импорта модуля.

    `client`/`reporter_client` — та же инъекция для тестов, что и в
    `run_agent`/`summarize_progress`: без неё `ChatClient` по умолчанию читает
    и пишет реальный `~/.pko/llm-cache`.

    `reporter`, в отличие от единого агента (`spec`), необязателен — без него
    (`None`) сводный вывод просто не формируется, весь остальной отчёт
    собирается как обычно.
    """
    try:
        from pko.progress.pptx_reader import read_deck
    except ImportError as exc:
        raise PkoError(
            "Не установлен python-pptx.",
            hint="поставьте пакет: pip install python-pptx>=1.0",
        ) from exc

    slides = read_deck(plan_path)
    result = run_agent(slides, target.tree, spec, client=client, max_steps=max_steps)
    if not result.usable:
        raise PkoError(
            "Не удалось получить пункты плана и вердикты.",
            hint="проверьте текст слайдов и доступность LLM; причина: "
                 + ("; ".join(result.notes) or "неизвестна"),
        )

    unclaimed = find_unclaimed_paths(target.extraction, result.verdicts)
    generated_at = time.strftime("%Y-%m-%d %H:%M")
    model = ProgressModel(
        meta={
            "repo": repo_name, "branch": target.branch, "commit": target.sha,
            "plan_source": plan_path.name, "generated_at": generated_at,
        },
        items={item.id: item for item in result.items},
        verdicts=result.verdicts,
        unclaimed=unclaimed,
        gaps=result.notes,
    )

    summary = summarize_progress(model, reporter, client=reporter_client)
    model.summary = summary.text
    model.summary_source = summary.source
    model.gaps.extend(summary.notes)
    return model
