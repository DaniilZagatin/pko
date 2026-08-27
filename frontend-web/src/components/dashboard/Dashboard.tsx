import type { AnalysisResult } from "@/lib/types";
import { DashboardHeader } from "./DashboardHeader";
import { Chevron } from "./Chevron";
import { StageDescription } from "./StageDescription";
import { StageComment } from "./StageComment";
import { StatusLegend } from "./StatusLegend";

// Три слоя, все раскрашены по статусу пункта — шевроны, описания этапов,
// комментарии модели. Читатель — руководитель, не разработчик: путей к
// файлам/тестов/технических деталей здесь нет и в самих данных с backend
// (backend/pko/web/analyses.py::_dashboard_json).
export function Dashboard({ analysis }: { analysis: AnalysisResult }) {
  return (
    <div className="flex flex-col gap-6">
      <DashboardHeader meta={analysis.meta} readiness={analysis.readiness} />
      <div className="rounded-xl border border-border bg-card p-6 flex flex-col gap-4">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <h2 className="text-sm font-semibold text-muted-foreground">Этапы проекта</h2>
          <StatusLegend />
        </div>
        <div className="journey-row">
          {analysis.items.map((item, i) => (
            <Chevron key={i} index={i} title={item.title} color={item.color} pct={item.pct} />
          ))}
        </div>
        <div className="journey-row">
          {analysis.items.map((item, i) => (
            <StageDescription key={i} text={item.description} color={item.color} />
          ))}
        </div>
        <div className="journey-row">
          {analysis.items.map((item, i) => (
            <StageComment key={i} text={item.explanation} color={item.color} />
          ))}
        </div>
      </div>
    </div>
  );
}
