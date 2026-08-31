"""Веб API `pko progress`: асинхронный анализ + живой прогресс по SSE.

Локальный инструмент: сервер поднимается на своей машине, git-доступ — через
уже настроенный SSH-agent (см. README). Логика пайплайна не дублируется с
CLI — обе стороны зовут один и тот же `pko.progress.pipeline.run_progress`,
поэтому веб и `pko progress` не могут разойтись в поведении.

UI — отдельный Next.js-проект `frontend-web/` (свой dev/прод-процесс, ходит
сюда через `/api/*`), этот модуль отдаёт только JSON и SSE, HTML не рендерит и
не раздаёт статику. Старая страница `frontend/index.html` и её синхронный
`POST /api/progress` (единственный запрос на весь прогон, блокирующий до
готовности) удалены вместе с этим модулем — реальный прогон может занимать от
секунд до ~15 минут только на клонирование репозитория
(`progress/target_repo.py`, `network_timeout=900`) плюс агентная сессия до 60
шагов, и ждать это за одним запросом без обратной связи не годится.
"""

from __future__ import annotations

import json
import queue

import anyio
from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.requests import Request
from fastapi.responses import JSONResponse, StreamingResponse

from pko.errors import PkoError
from pko.llm.registry import get_spec
from pko.web import analyses
from pko.web import products as products_web

app = FastAPI(title="PKO progress")


@app.exception_handler(PkoError)
def _handle_pko_error(request: Request, exc: PkoError) -> JSONResponse:
    # Тот же контракт ошибки, что печатает CLI (`exc.render()`), но в JSON:
    # страница показывает message и hint отдельно, а не голый стек.
    return JSONResponse(status_code=400, content={"message": exc.message, "hint": exc.hint})


# Пауза между шагами реального агента (не скриптованного тестового LLM)
# может доходить до `ChatClient.timeout` (120с, backend/pko/llm/client.py) —
# без периодического события простаивающее HTTP-соединение рискует быть
# закрытым промежуточным прокси/балансировщиком, у которых idle-таймаут
# часто короче. `: ...\n\n` — SSE-комментарий: `EventSource` браузера его
# не отдаёт как `message`, только держит соединение видимо активным.
_HEARTBEAT_SECONDS = 15


def _get_job_or_404(analysis_id: str) -> analyses.AnalysisJob:
    job = analyses.get_job(analysis_id)
    if job is None:
        raise PkoError("Анализ не найден.", hint=f"неизвестный analysis_id: {analysis_id!r}")
    return job


@app.post("/api/analyses")
async def create_analysis(
    presentation: UploadFile = File(...),
    repository: str = Form(""),
    branch: str = Form(""),
    product_id: str = Form(""),
    files: list[UploadFile] = File([]),
) -> JSONResponse:
    """`repository` и `files` — два независимых необязательных источника
    evidence (см. докстринг `analyses.create_analysis`), не выбор одного из
    вариантов — можно и репозиторий, и файлы поверх (например
    `metrics.json`, которого нет в самом репозитории). Оба пустые — тоже
    валидный запрос: агент получает пустой снимок материалов и сам решает,
    что писать в вердикт.

    `product_id` — тоже необязателен: пусто — прежнее разовое поведение без
    сохранения в историю; задан — результат ляжет в `pko.store` как snapshot
    этого продукта (см. `analyses.create_analysis`/`_execute`).
    """
    spec = get_spec("matcher")
    if spec is None:
        raise PkoError(
            "Не настроен LLM-доступ для пайплайна прогресса.",
            hint="задайте PKO_ASSEMBLER_BASE_URL/PKO_ASSEMBLER_MODEL/PKO_ASSEMBLER_API_KEY "
                 "перед запуском `pko serve` — роль matcher использует его по умолчанию; "
                 "либо настройте PKO_MATCHER_* отдельно.",
        )
    # Необязательная роль — без неё просто не будет сводного вывода в модели.
    reporter = get_spec("reporter")

    # `files=[UploadFile()]` пустышка — Starlette так отдаёт поле, которое
    # клиент вообще не передал в multipart-форме, а не пустой список; отличать
    # её от настоящего файла с пустым именем не нужно — с пустым именем
    # `local_source._safe_relative_path` его всё равно отклонит как файл.
    uploads = [
        (f.filename, await f.read())
        for f in files
        if f.filename
    ]

    job = analyses.create_analysis(
        presentation_bytes=await presentation.read(),
        presentation_filename=presentation.filename or "",
        repository=repository,
        branch=branch,
        uploads=uploads,
        spec=spec,
        reporter=reporter,
        product_id=product_id,
    )
    return JSONResponse({"analysis_id": job.id, "status": job.status})


@app.get("/api/analyses/{analysis_id}/events")
async def stream_analysis_events(analysis_id: str) -> StreamingResponse:
    job = _get_job_or_404(analysis_id)

    async def generate():
        # Задача могла завершиться раньше, чем клиент открыл это соединение
        # (например, `EventSource` браузера переподключается после разрыва
        # сети) — тогда очередь уже пуста и блокирующий `get()` повис бы
        # навсегда. В этом случае сразу отдаём терминальное событие вместо
        # чтения из очереди.
        if job.status != "PROCESSING":
            terminal = {"type": "analysis_ready"} if job.status == "READY" else {"type": "error", **(job.error or {})}
            yield f"data: {json.dumps(terminal, ensure_ascii=False)}\n\n"
            return

        while True:
            # `queue.Queue.get` блокирует поток — уводим в threadpool, чтобы не
            # держать event loop; сама очередь на задачу одна (один подписчик).
            # Таймаут — не для отмены, а чтобы регулярно возвращаться сюда и
            # отдать heartbeat, если реальный агент надолго задумался.
            try:
                event = await anyio.to_thread.run_sync(lambda: job.events.get(timeout=_HEARTBEAT_SECONDS))
            except queue.Empty:
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("type") in ("analysis_ready", "error"):
                break

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/api/analyses/{analysis_id}")
async def get_analysis(analysis_id: str) -> JSONResponse:
    job = _get_job_or_404(analysis_id)
    if job.status == "PROCESSING":
        return JSONResponse({"status": "PROCESSING"})
    if job.status == "ERROR":
        return JSONResponse(status_code=400, content={"status": "ERROR", **(job.error or {})})
    return JSONResponse({"status": "READY", **(job.result or {})})


# --- продукты и история проверок (Progress Mode) ---------------------------
# Сам прогон анализа по-прежнему идёт через /api/analyses (выше) — эти роуты
# только про хранение результата под продуктом и его выдачу обратно.

@app.post("/api/products")
async def create_product(name: str = Form(...)) -> JSONResponse:
    return JSONResponse(products_web.create_product(name))


@app.get("/api/products")
async def list_products() -> JSONResponse:
    return JSONResponse(products_web.list_products())


@app.get("/api/products/{product_id}")
async def get_product_detail(product_id: str) -> JSONResponse:
    return JSONResponse(products_web.get_product(product_id))


@app.get("/api/products/{product_id}/snapshots")
async def list_product_snapshots(product_id: str) -> JSONResponse:
    return JSONResponse(products_web.list_snapshots(product_id))


@app.get("/api/products/{product_id}/snapshots/{snapshot_id}")
async def get_product_snapshot(product_id: str, snapshot_id: str) -> JSONResponse:
    return JSONResponse(products_web.get_snapshot_dashboard(product_id, snapshot_id))


@app.get("/api/products/{product_id}/compare")
async def compare_product_snapshots(
    product_id: str,
    from_: str = Query("", alias="from"),
    to: str = Query(""),
) -> JSONResponse:
    # `from` — зарезервированное слово Python, поэтому параметр функции —
    # `from_`; `alias="from"` заставляет FastAPI читать его из `?from=...`,
    # как задокументировано в плане версионирования (§30). `reporter`, как и
    # в `create_analysis`, необязателен: без него сравнение отдаётся с
    # пустой бизнес-интерпретацией, а не ошибкой.
    reporter = get_spec("reporter")
    return JSONResponse(products_web.compare_snapshots(product_id, from_, to, reporter))
