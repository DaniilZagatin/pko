// Зеркало JSON-контракта backend/pko/web/analyses.py::_dashboard_json и
// событий SSE из backend/pko/web/app.py::stream_analysis_events. Значения
// `color`/`status` — те же строки, что и в backend/pko/render/progress_report.py
// (STATUS_LABELS): "green"/"amber"/"red"/"purple", "DONE"/"PARTIAL"/
// "NOT_STARTED"/"UNCLEAR".

export interface StageItem {
  title: string;
  description: string;
  status: "DONE" | "PARTIAL" | "NOT_STARTED" | "UNCLEAR";
  label: string;
  color: "green" | "amber" | "red" | "purple";
  pct: number;
  explanation: string;
}

export interface AnalysisMeta {
  repo: string;
  branch: string;
  commit: string;
  plan_source: string;
  generated_at: string;
}

// Структурированный сводный вывод по всему проекту (роль reporter на
// стороне backend). Опционально: пока backend отдаёт его как единый текст
// "всё в одном", а не в этом виде — поле отсутствует в реальном ответе API,
// пока backend не начнёт возвращать эти три части отдельно. `ProjectSummaryPanel`
// сама не рендерится, если `summary` не пришёл.
export interface ProjectSummary {
  conclusion: string;
  risks: string[];
  priorities: string[];
}

export interface AnalysisResult {
  status: "READY";
  meta: AnalysisMeta;
  readiness: number;
  counts: Record<StageItem["status"], number>;
  items: StageItem[];
  summary?: ProjectSummary;
}

export interface AnalysisPending {
  status: "PROCESSING";
}

export interface AnalysisFailed {
  status: "ERROR";
  message: string;
  hint: string;
}

export type Analysis = AnalysisResult | AnalysisPending | AnalysisFailed;

export interface CreateAnalysisResponse {
  analysis_id: string;
  status: "PROCESSING";
}

export interface ApiErrorBody {
  message: string;
  hint: string;
}

// События SSE (`GET /api/analyses/{id}/events`) — ровно то, что кладёт в
// очередь backend/pko/web/analyses.py::_execute (`emit("phase", {...})` —
// только materials_loading/materials_ready, источник-нейтральные: и git, и
// файлы, и оба сразу проходят через одни и те же два имени) и адаптер
// `on_event`, передаваемый в `run_progress` (`presentation_parsed`/
// `claim_verified`/`summarizing` — прилетают как отдельные `type`, не
// завёрнутые в "phase").
export type PhaseName = "materials_loading" | "materials_ready";

export type AnalysisEvent =
  | { type: "phase"; phase: PhaseName; label: string }
  | { type: "presentation_parsed"; slide_count: number }
  | { type: "claim_verified"; title: string; status: StageItem["status"] }
  | { type: "summarizing" }
  | { type: "analysis_ready" }
  | { type: "error"; message: string; hint: string };
