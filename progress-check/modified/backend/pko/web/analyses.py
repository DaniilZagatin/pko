"""Асинхронные задачи анализа для живого веб-фронтенда (`frontend-web/`).

Локальный инструмент, один процесс — как и весь `pko serve` (см. докстринг
`web/app.py`). Поэтому реестр задач — обычный `dict` в памяти, выполнение —
`threading.Thread`, обмен событиями прогресса — `queue.Queue`: без
БД/очереди/Redis, ровно тот же принцип простоты, что и у остального PKO.
Задачи не переживают перезапуск сервера — сознательное ограничение, не
случайный пробел.

Один прогон = один `AnalysisJob`. `run_progress` (`progress.pipeline`) уже
умеет сообщать о прогрессе через `on_event` — здесь это событие просто
складывается в очередь задачи, откуда его читает SSE-эндпоинт
(`web/app.py::stream_analysis_events`).
"""

from __future__ import annotations

import hashlib
import queue
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pko.errors import PkoError
from pko.llm.registry import ModelSpec
from pko.progress.local_source import (
    build_empty_workspace,
    build_target_repo_from_uploads,
    merge_with_uploads,
)
from pko.progress.pipeline import run_progress
from pko.progress.schema import ProgressModel
from pko.progress.target_repo import TargetRepo, load_target, open_repo_source
from pko.render.progress_report import STATUS_LABELS, display_percent
from pko.store import products as products_store
from pko.versioning.canonical import save_snapshot_with_matching

Status = Literal["PROCESSING", "READY", "ERROR"]


@dataclass
class AnalysisJob:
    id: str
    status: Status = "PROCESSING"
    events: "queue.Queue[dict[str, Any]]" = field(default_factory=queue.Queue)
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None


_JOBS: dict[str, AnalysisJob] = {}


def get_job(job_id: str) -> AnalysisJob | None:
    return _JOBS.get(job_id)


def dashboard_json(model: ProgressModel) -> dict[str, Any]:
    """То, что реально рисует dashboard — не полный `ProgressModel.to_dict()`.

    Читатель dashboard — руководитель, не разработчик: пути к файлам, тесты,
    `unclaimed`/`gaps` сюда осознанно не попадают, тем же принципом, что уже
    применён к HTML-отчёту (`render/progress_report.py`). `STATUS_LABELS`/
    `display_percent` переиспользуются оттуда же — одна палитра статусов на
    оба представления, а не две копии, которые могут разойтись.
    """
    items = []
    for verdict in model.verdicts:
        item = model.items.get(verdict.item_id)
        title = item.title if item else verdict.item_id
        description = (item.description if item and item.description else "") or title
        label, color = STATUS_LABELS[verdict.status]
        items.append({
            "title": title,
            "description": description,
            "status": verdict.status,
            "label": label,
            "color": color,
            "pct": display_percent(verdict),
            "explanation": verdict.explanation,
        })
    return {
        "meta": model.meta,
        "readiness": round(model.progress_ratio(), 4),
        "counts": model.counts(),
        "items": items,
    }


def create_analysis(
    presentation_bytes: bytes,
    presentation_filename: str,
    repository: str,
    branch: str,
    uploads: list[tuple[str, bytes]],
    spec: ModelSpec,
    reporter: ModelSpec | None,
    product_id: str = "",
) -> AnalysisJob:
    """Провалидировать вход, создать задачу и сразу запустить её в фоне.

    `repository` и `uploads` — два независимых необязательных источника
    evidence, не выбор одного из вариантов: репозиторий даёт код, файлы —
    то, что в него не попало (результат эксперимента, отчёт, метрики).
    Заполнены оба сразу — `_execute` объединяет их в один workspace
    (`progress/local_source.py::merge_with_uploads`). Не заполнен ни
    один — это НЕ ошибка запроса: агент всё равно запускается, просто видит
    пустой снимок материалов (`local_source.build_empty_workspace`) и сам
    решает, что писать в вердикт — тем же путём, что и «в репозитории
    ничего не нашлось» (см. `_AGENT_SYSTEM` в `matcher.py`). Что именно
    показывать в этом случае, намеренно не диктуется здесь заранее.

    `product_id` — тоже необязателен (пустая строка): разовая проверка без
    сохранения в историю остаётся доступна как раньше. Заполнен — значит
    результат должен лечь в `pko.store` как очередной snapshot этого
    продукта; продукт обязан существовать уже на этом этапе (падает в ответе
    на POST, а не молча в фоновом потоке).

    Валидация — та же, что раньше была в начале синхронного `api_progress`:
    падает сразу в ответе на `POST`, до создания задачи и потока, а не
    всплывает позже как молчаливо зависшая задача.
    """
    if not presentation_filename or not presentation_filename.lower().endswith(".pptx"):
        raise PkoError(
            "Файл плана должен быть .pptx.",
            hint=f"получено имя файла: {presentation_filename!r}",
        )
    product_id = product_id.strip()
    if product_id and products_store.get_product(product_id) is None:
        raise PkoError(
            "Продукт не найден.",
            hint=f"неизвестный product_id: {product_id!r} — обновите список продуктов",
        )

    job = AnalysisJob(id="an_" + uuid.uuid4().hex[:12])
    _JOBS[job.id] = job

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"pko-analysis-{job.id}-"))
    plan_path = tmp_dir / Path(presentation_filename).name
    plan_path.write_bytes(presentation_bytes)

    thread = threading.Thread(
        target=_execute,
        args=(job, plan_path, repository.strip(), branch.strip() or None, uploads, tmp_dir,
              spec, reporter, product_id),
        daemon=True,
    )
    thread.start()
    return job


def _build_target(
    repository: str, branch: str | None, uploads: list[tuple[str, bytes]], workspace_dir: Path,
    emit,
) -> tuple[TargetRepo, str]:
    """Git, файлы, или оба сразу — см. докстринг `create_analysis`."""
    target: TargetRepo | None = None
    name = ""
    if repository:
        emit("phase", {"phase": "materials_loading", "label": "Подключаем репозиторий"})
        git_repo, name = open_repo_source(repository, branch=branch)
        target = load_target(git_repo, branch)
    else:
        emit("phase", {"phase": "materials_loading", "label": "Обрабатываем загруженные файлы"})

    if uploads:
        target = (
            merge_with_uploads(target, uploads, workspace_dir) if target is not None
            else build_target_repo_from_uploads(uploads, workspace_dir)
        )
    elif target is None:
        # Ни репозитория, ни файлов — см. докстринг create_analysis: не
        # ошибка, агент получает пустой снимок и сам решает, что с этим делать.
        target = build_empty_workspace(workspace_dir)
    emit("phase", {"phase": "materials_ready", "label": "Материалы проекта готовы"})
    return target, name


def _describe_source(
    repository: str, target: TargetRepo, uploads: list[tuple[str, bytes]]
) -> dict[str, Any]:
    """Метаданные материалов для воспроизводимости snapshot'а (не самого

    workspace — тот временный и удаляется вместе с `tmp_dir`). Git и файлы —
    независимые поля, как и на входе `create_analysis`: заполнены могут быть
    оба сразу.
    """
    source: dict[str, Any] = {}
    if repository:
        source["repo"] = {"repository": repository, "branch": target.branch, "commit": target.sha}
    if uploads:
        source["files"] = [
            {"filename": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in uploads
        ]
    return source


def _execute(
    job: AnalysisJob,
    plan_path: Path,
    repository: str,
    branch: str | None,
    uploads: list[tuple[str, bytes]],
    tmp_dir: Path,
    spec: ModelSpec,
    reporter: ModelSpec | None,
    product_id: str = "",
) -> None:
    def emit(kind: str, data: dict[str, Any]) -> None:
        job.events.put({"type": kind, **data})

    try:
        target, name = _build_target(repository, branch, uploads, tmp_dir / "workspace", emit)
        model = run_progress(plan_path, name, target, spec, reporter=reporter, on_event=emit)

        job.result = dashboard_json(model)
        if product_id:
            source = _describe_source(repository, target, uploads)
            # canonical_stage_id проставляется здесь, а не в `run_progress` —
            # ПОСЛЕ того, как модель уже независима от истории продукта (план
            # версионирования, §6): агент, оценивавший текущую версию, не
            # видел прошлый snapshot и не мог сместить оценку в его сторону.
            snapshot = save_snapshot_with_matching(product_id, model, source, spec)
            job.result.update({
                "product_id": product_id,
                "snapshot_id": snapshot.id,
                "version_number": snapshot.version_number,
            })
        job.status = "READY"
        job.events.put({"type": "analysis_ready"})
    except PkoError as exc:
        job.error = {"message": exc.message, "hint": exc.hint}
        job.status = "ERROR"
        job.events.put({"type": "error", **job.error})
    except Exception as exc:  # noqa: BLE001 — граница фонового потока: без этого
        # необработанное исключение молча оседает в потоке (traceback только в
        # stderr процесса), а SSE-клиент завис бы навсегда без единого
        # терминального события. Дальше по стеку это не поймать — здесь конец пути.
        job.error = {"message": "Внутренняя ошибка анализа.", "hint": str(exc)}
        job.status = "ERROR"
        job.events.put({"type": "error", **job.error})
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
