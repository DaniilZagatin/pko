import type { Analysis, ApiErrorBody, CreateAnalysisResponse } from "./types";

// `/api/*` — в dev проксируется на FastAPI через rewrites() в next.config.ts,
// в проде оба процесса стоят за одним обратным прокси (см. README) — здесь
// достаточно относительного пути, свой базовый URL не нужен.

export class ApiError extends Error {
  hint: string;
  constructor(body: ApiErrorBody) {
    super(body.message);
    this.hint = body.hint;
  }
}

async function parseOrThrow<T>(resp: Response): Promise<T> {
  const body = await resp.json();
  if (!resp.ok) {
    throw new ApiError(body as ApiErrorBody);
  }
  return body as T;
}

export async function createAnalysis(
  presentation: File,
  repository: string,
  branch: string,
  files: File[]
): Promise<CreateAnalysisResponse> {
  // Репозиторий и файлы — два независимых необязательных источника evidence,
  // не выбор одного из вариантов (см. backend/pko/web/analyses.py::create_analysis)
  // — можно и репозиторий, и файлы поверх; сервер требует хотя бы один.
  const form = new FormData();
  form.set("presentation", presentation);
  form.set("repository", repository);
  form.set("branch", branch);
  for (const file of files) {
    form.append("files", file);
  }
  const resp = await fetch("/api/analyses", { method: "POST", body: form });
  return parseOrThrow<CreateAnalysisResponse>(resp);
}

export async function getAnalysis(analysisId: string): Promise<Analysis> {
  const resp = await fetch(`/api/analyses/${analysisId}`);
  // ERROR-статус тоже приходит с HTTP 400 (см. web/app.py::get_analysis) —
  // это по-прежнему валидный Analysis, а не ApiError: сама задача найдена и
  // выполнилась, просто с ошибкой пайплайна, а не проблема с запросом.
  const body = await resp.json();
  if (!resp.ok && body.status !== "ERROR") {
    throw new ApiError(body as ApiErrorBody);
  }
  return body as Analysis;
}
