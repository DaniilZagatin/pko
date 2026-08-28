import { CheckCircle2, CircleDashed, HelpCircle, Loader2, XCircle } from "lucide-react";
import type { AnalysisEvent, PhaseName } from "@/lib/types";

// Backend не диктует готовые UI-строки — только `phase`/`type`/`title`/
// `status` (см. backend/pko/web/analyses.py, backend/pko/progress/pipeline.py).
// Соответствие бизнес-тексту — целиком дело фронтенда.
const PHASE_LABELS: Record<PhaseName, string> = {
  materials_loading: "Обрабатываем материалы проекта",
  materials_ready: "Материалы проекта готовы",
};

// Цвет — тот же CSS-var, что и у вердиктов дашборда (--green/--amber/--red/
// --purple, см. globals.css), а не отдельная Tailwind-палитра только для
// иконок. Иконка стоит рядом с текстом статуса, который уже несёт смысл, —
// она декоративна и скрыта от скринридера (aria-hidden), а не отдельная
// текстовая альтернатива.
const STATUS_ICON: Record<string, { Icon: typeof CheckCircle2; color: string }> = {
  DONE: { Icon: CheckCircle2, color: "var(--green)" },
  PARTIAL: { Icon: CircleDashed, color: "var(--amber)" },
  NOT_STARTED: { Icon: XCircle, color: "var(--red)" },
  UNCLEAR: { Icon: HelpCircle, color: "var(--purple)" },
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

  // Один короткий атомарный статус в aria-live, а не весь растущий список
  // пунктов — иначе на каждый новый claim скринридер перечитывал бы всю
  // историю целиком (ux-guidelines: contextual-live-badge-updates).
  const liveStatus = claims.length > 0
    ? `Проверено пунктов плана: ${claims.length}`
    : phases[phases.length - 1] ?? "Начинаем анализ";

  return (
    <div className="rounded-xl border border-border bg-card p-8 flex flex-col gap-4">
      <h1 className="text-lg font-semibold text-center">Анализируем проект</h1>
      <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
        {liveStatus}
      </p>
      <ul className="flex flex-col gap-2 text-sm">
        {phases.map((text, i) => (
          <li key={i} className="flex items-center gap-2 text-foreground">
            <CheckCircle2 className="size-4 shrink-0" aria-hidden="true" />
            {text}
          </li>
        ))}
        {phases.length > 0 && phases.length < 3 && claims.length === 0 && (
          <li className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="size-4 shrink-0 animate-spin" aria-hidden="true" />
            Сопоставляем план с текущим состоянием проекта
          </li>
        )}
      </ul>
      {claims.length > 0 && (
        <ul className="flex flex-col gap-2 text-sm border-t border-border pt-3">
          {claims.map((claim, i) => {
            if (claim.type !== "claim_verified") return null;
            const { Icon, color } = STATUS_ICON[claim.status];
            return (
              <li key={i} className="flex items-center gap-2 text-muted-foreground">
                <Icon className="size-4 shrink-0" style={{ color }} aria-hidden="true" />
                {claim.title}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
