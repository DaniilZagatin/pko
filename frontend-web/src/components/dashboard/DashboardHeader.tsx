import type { AnalysisMeta } from "@/lib/types";

export function DashboardHeader({
  meta, readiness,
}: {
  meta: AnalysisMeta;
  readiness: number;
}) {
  const pct = Math.round(readiness * 100);

  return (
    <div className="rounded-xl border border-border bg-card p-6 flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <p className="text-muted-foreground text-xs">Анализ готовности проекта</p>
          <h1 className="text-lg font-semibold">{meta.repo || "Проект"}</h1>
        </div>
        <div className="text-right">
          <div className="text-3xl font-bold text-primary">{pct}%</div>
          <div className="text-muted-foreground text-xs">готовность проекта</div>
        </div>
      </div>
      <div className="h-2 rounded-full bg-muted overflow-hidden">
        <div className="h-full rounded-full bg-primary" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
