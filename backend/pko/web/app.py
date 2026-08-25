"""Веб-интерфейс `pko progress`: одна страница + один эндпоинт.

Локальный инструмент: сервер поднимается на своей машине, git-доступ — через
уже настроенный SSH-agent (см. README). Логика пайплайна не дублируется с
CLI — обе стороны зовут один и тот же `pko.progress.pipeline.run_progress`,
поэтому веб и `pko progress` не могут разойтись в поведении.

Бэкенд (`backend/pko/`) и фронтенд (`frontend/`) — раздельные каталоги
верхнего уровня репозитория: страница не является Python package data, `pip
install` пакета её не тянет. Путь к ней вычисляется относительно корня
репозитория, а не пакета — оба каталога живут рядом по построению.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse

from pko.errors import PkoError
from pko.llm.registry import get_spec
from pko.progress.pipeline import run_progress
from pko.progress.target_repo import load_target, open_repo_source
from pko.render.progress_report import render_progress_report

# app.py -> web -> pko -> backend -> корень репозитория, рядом с ним frontend/.
REPO_ROOT = Path(__file__).resolve().parents[3]
FRONTEND_DIR = REPO_ROOT / "frontend"

app = FastAPI(title="PKO progress")


@app.exception_handler(PkoError)
def _handle_pko_error(request: Request, exc: PkoError) -> JSONResponse:
    # Тот же контракт ошибки, что печатает CLI (`exc.render()`), но в JSON:
    # страница показывает message и hint отдельно, а не голый стек.
    return JSONResponse(status_code=400, content={"message": exc.message, "hint": exc.hint})


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")


@app.post("/api/progress")
async def api_progress(
    plan: UploadFile = File(...),
    repo: str = Form(...),
    branch: str = Form(""),
) -> JSONResponse:
    if not plan.filename or not plan.filename.lower().endswith(".pptx"):
        raise PkoError(
            "Файл плана должен быть .pptx.",
            hint=f"получено имя файла: {plan.filename!r}",
        )
    if not repo.strip():
        raise PkoError("Не указан репозиторий.", hint="SSH-ссылка или локальный путь")

    planner = get_spec("planner")
    matcher_spec = get_spec("matcher")
    if planner is None or matcher_spec is None:
        raise PkoError(
            "Не настроен LLM-доступ для пайплайна прогресса.",
            hint="задайте PKO_ASSEMBLER_BASE_URL/PKO_ASSEMBLER_MODEL/PKO_ASSEMBLER_API_KEY "
                 "перед запуском `pko serve` — роли planner/matcher используют его по "
                 "умолчанию; либо настройте PKO_PLANNER_*/PKO_MATCHER_* отдельно.",
        )

    with tempfile.TemporaryDirectory(prefix="pko-progress-upload-") as tmp:
        plan_path = Path(tmp) / Path(plan.filename).name
        plan_path.write_bytes(await plan.read())

        git_repo, name = open_repo_source(repo.strip(), branch=branch.strip() or None)
        target = load_target(git_repo, branch.strip() or None)
        model = run_progress(plan_path, name, target, planner, matcher_spec)

    return JSONResponse({
        "html": render_progress_report(model),
        "counts": model.counts(),
        "progress_ratio": model.progress_ratio(),
        "meta": model.meta,
    })
