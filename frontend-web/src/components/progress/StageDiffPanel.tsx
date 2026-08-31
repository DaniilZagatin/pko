import { statusLabel } from "@/lib/status";
import type { SnapshotSummary, StageDelta } from "@/lib/types";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "long" });
}

// Клик по этапу в Progress Mode открывает не описание этапа (как в «Текущем
// состоянии»), а именно изменения — план версионирования, §22/23: «Что
// изменилось» текстом, а не голое старое/новое значение рядом.
export function StageDiffPanel({
  delta, from, to,
}: { delta: StageDelta; from: SnapshotSummary; to: SnapshotSummary }) {
  return (
    <div className="rounded-xl border-2 border-border bg-card p-5 flex flex-col gap-4">
      <div className="flex items-center justify-between gap-4 flex-wrap text-sm">
        <span className="text-muted-foreground">{formatDate(from.created_at)}</span>
        <span className="font-semibold text-foreground">
          {statusLabel(delta.previous_status)} → {statusLabel(delta.current_status)}
        </span>
        <span className="text-muted-foreground">{formatDate(to.created_at)}</span>
      </div>
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-semibold text-foreground">Что изменилось</h3>
        <p className="text-sm text-muted-foreground leading-relaxed">
          {delta.business_delta || "Бизнес-объяснение по этому этапу пока не сформировано."}
        </p>
      </div>
    </div>
  );
}
