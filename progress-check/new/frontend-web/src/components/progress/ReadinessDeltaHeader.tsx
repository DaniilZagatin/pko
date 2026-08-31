import type { SnapshotSummary, VersionComparison } from "@/lib/types";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "long", year: "numeric" });
}

// Верхняя часть экрана «Прогресс» (план версионирования, §12): главное —
// FROM -> TO и общая динамика, без деталей по этапам — те ниже, в шевронах.
export function ReadinessDeltaHeader({
  from, to, comparison,
}: { from: SnapshotSummary; to: SnapshotSummary; comparison?: VersionComparison }) {
  const before = Math.round((comparison?.readiness_before ?? from.overall_readiness) * 100);
  const after = Math.round((comparison?.readiness_after ?? to.overall_readiness) * 100);
  const deltaPp = after - before;

  const improved = comparison?.stage_deltas.filter((d) => d.change_type === "IMPROVED").length ?? 0;
  const unchanged = comparison?.stage_deltas.filter((d) => d.change_type === "UNCHANGED").length ?? 0;
  const regressed = comparison?.stage_deltas.filter((d) => d.change_type === "REGRESSED").length ?? 0;
  const added = comparison?.stage_deltas.filter((d) => d.change_type === "ADDED").length ?? 0;

  return (
    <div className="rounded-xl border border-border bg-card p-6 flex flex-col gap-4">
      <div className="flex items-center justify-center gap-6 flex-wrap">
        <div className="text-center">
          <div className="text-xs text-muted-foreground">{formatDate(from.created_at)}</div>
          <div className="text-3xl font-bold tabular-nums">{before}%</div>
        </div>
        <div className="text-muted-foreground text-xl" aria-hidden="true">→</div>
        <div className="text-center">
          <div className="text-xs text-muted-foreground">{formatDate(to.created_at)}</div>
          <div className="text-3xl font-bold text-primary tabular-nums">{after}%</div>
        </div>
      </div>
      <div
        className="text-center text-sm font-semibold tabular-nums"
        style={{ color: deltaPp >= 0 ? "var(--green)" : "var(--red)" }}
      >
        {deltaPp >= 0 ? "+" : ""}{deltaPp} п.п.
      </div>
      {comparison && (
        <div className="flex items-center justify-center gap-4 flex-wrap text-xs text-muted-foreground">
          <span>{improved} {pluralStage(improved)} улучшил{improved === 1 ? "ся" : "ись"}</span>
          <span>{unchanged} без изменений</span>
          <span>{regressed} ухудшил{regressed === 1 ? "ся" : "ись"}</span>
          {added > 0 && <span>{added} нов{added === 1 ? "ый" : "ых"} {pluralStage(added)}</span>}
        </div>
      )}
    </div>
  );
}

function pluralStage(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return "этап";
  if ([2, 3, 4].includes(mod10) && ![12, 13, 14].includes(mod100)) return "этапа";
  return "этапов";
}
