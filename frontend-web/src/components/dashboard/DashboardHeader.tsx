import { Clock } from "lucide-react";
import type { AnalysisMeta } from "@/lib/types";

export function DashboardHeader({
  meta, readiness,
}: {
  meta: AnalysisMeta;
  readiness: number;
}) {
  const pct = Math.round(readiness * 100);
  const generatedAt = meta.generated_at
    ? new Date(meta.generated_at).toLocaleString("ru-RU", { dateStyle: "medium", timeStyle: "short" })
    : null;

  return (
    <div className="rounded-xl border border-border bg-card p-6 flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex flex-col gap-1.5">
          <p className="text-muted-foreground text-xs">Анализ готовности проекта</p>
          <h1 className="text-lg font-semibold">{meta.repo || "Проект"}</h1>
          {generatedAt && (
            <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <Clock className="size-3.5 shrink-0" aria-hidden="true" />
              {generatedAt}
            </span>
          )}
        </div>
        <div className="text-right">
          <div className="text-4xl font-bold text-primary tabular-nums">{pct}%</div>
          <div className="text-muted-foreground text-xs">готовность проекта</div>
        </div>
      </div>
      <div className="h-2.5 rounded-full bg-muted overflow-hidden">
        <div
          className="h-full rounded-full bg-primary transition-[width] duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
