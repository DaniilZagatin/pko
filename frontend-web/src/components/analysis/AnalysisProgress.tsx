import type { AnalysisEvent, PhaseName } from "@/lib/types";

// Backend не диктует готовые UI-строки — только `phase`/`type`/`title`/
// `status` (см. backend/pko/web/analyses.py, backend/pko/progress/pipeline.py).
// Соответствие бизнес-тексту — целиком дело фронтенда.
const PHASE_LABELS: Record<PhaseName, string> = {
  repository_cloning: "Подключаем репозиторий",
  repository_ready: "Репозиторий подключён",
};

const STATUS_ICON: Record<string, string> = {
  DONE: "✓", PARTIAL: "◐", NOT_STARTED: "✕", UNCLEAR: "?",
};

function describe(event: AnalysisEvent): string | null {
  switch (event.type) {
    case "phase":
      return PHASE_LABELS[event.phase];
    case "presentation_parsed":
      return "Презентация обработана";
    case "summarizing":
      return "Формируем выводы для руководства";
    default:
      // claim_verified рисуется отдельным растущим списком ниже, а не здесь;
      // analysis_ready/error не показываются как строка прогресса.
      return null;
  }
}

export function AnalysisProgress({ events }: { events: AnalysisEvent[] }) {
  const phases = events.map(describe).filter((text): text is string => text !== null);
  const claims = events.filter((e) => e.type === "claim_verified");

  return (
    <div className="rounded-xl border border-border bg-card p-8 flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-center">Анализируем проект</h1>
      <ul className="flex flex-col gap-2 text-sm">
        {phases.map((text, i) => (
          <li key={i} className="text-foreground">✓ {text}</li>
        ))}
        {phases.length > 0 && phases.length < 3 && claims.length === 0 && (
          <li className="text-muted-foreground">● Сопоставляем план с текущим состоянием проекта</li>
        )}
      </ul>
      {claims.length > 0 && (
        <ul className="flex flex-col gap-2 text-sm border-t border-border pt-3">
          {claims.map((claim, i) => (
            claim.type === "claim_verified" && (
              <li key={i} className="text-muted-foreground">
                {STATUS_ICON[claim.status]} {claim.title}
              </li>
            )
          ))}
        </ul>
      )}
    </div>
  );
}
