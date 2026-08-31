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
  // Присутствуют только если анализ был привязан к продукту
  // (backend/pko/web/analyses.py::_execute) — разовый анализ без продукта их
  // не несёт. `AnalysisView` использует это, чтобы уйти на страницу продукта
  // вместо разового dashboard.
  product_id?: string;
  snapshot_id?: string;
  version_number?: number;
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

// Зеркало backend/pko/store/products.py::ProductSummary.to_dict().
export interface Product {
  id: string;
  name: string;
  created_at: string;
  snapshot_count: number;
  latest_readiness: number | null;
  latest_created_at: string | null;
}

// Выбор продукта в форме анализа — привязка явная (пользователь выбирает или
// создаёт продукт сам), не автоматический матчинг по репозиторию: репозиторий
// переименовывают, а у файлового источника его вообще нет.
export type ProductSelection =
  | { mode: "none" }
  | { mode: "existing"; productId: string }
  | { mode: "new"; name: string };

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

// --- Progress Mode: история проверок продукта и сравнение снимков ---------
// Зеркало backend/pko/store/snapshots.py::Snapshot.summary_dict().
export interface SnapshotSummary {
  id: string;
  product_id: string;
  version_number: number;
  created_at: string;
  overall_readiness: number;
  source: Record<string, unknown>;
}

// Ответ GET /api/products/{id}/snapshots/{snapshot_id}
// (backend/pko/web/products.py::get_snapshot_dashboard) — тот же контракт,
// что и готовый анализ (dashboard_json), плюс метаданные самого снимка;
// нарочно без "status", в отличие от AnalysisResult — здесь снимок уже
// точно есть, ждать READY не нужно.
export interface SnapshotDashboard {
  meta: AnalysisMeta;
  readiness: number;
  counts: Record<StageItem["status"], number>;
  items: StageItem[];
  summary?: ProjectSummary;
  snapshot_id: string;
  version_number: number;
  created_at: string;
  source: Record<string, unknown>;
}

// Зеркало backend/pko/versioning/diff.py — change_type ограничен пятью
// значениями MVP, SCOPE_CHANGED сознательно не реализован (план
// версионирования относит его к «после MVP», §39).
export type ChangeType = "IMPROVED" | "UNCHANGED" | "REGRESSED" | "ADDED" | "REMOVED";

export interface StageDelta {
  canonical_stage_id: string;
  title: string;
  previous_status: StageItem["status"] | null;
  current_status: StageItem["status"] | null;
  previous_readiness: number | null;
  current_readiness: number | null;
  readiness_delta: number | null;
  change_type: ChangeType;
  business_delta: string;
}

export interface RiskItem {
  text: string;
  state: "NEW" | "PERSISTING" | "RESOLVED";
}

// Ответ GET /api/products/{id}/compare (backend/pko/web/products.py::compare_snapshots)
// — детерминированные факты (versioning/diff.py) слитые с необязательной
// LLM-интерпретацией (versioning/interpret.py); последняя может быть пустой
// (roль reporter не настроена или ещё не досчиталась), тогда
// progress_summary/current_risks/next_focus/business_delta — пустые
// значения, не ошибка.
export interface VersionComparison {
  readiness_before: number;
  readiness_after: number;
  readiness_delta: number;
  stage_deltas: StageDelta[];
  progress_summary: string;
  current_risks: RiskItem[];
  next_focus: string[];
}
