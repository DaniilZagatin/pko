"use client";

import { useState } from "react";
import type { AnalysisResult } from "@/lib/types";
import { DashboardHeader } from "./DashboardHeader";
import { Chevron } from "./Chevron";
import { StageDescriptionPanel } from "./StageDescriptionPanel";
import { StageEvaluationCard } from "./StageEvaluationCard";
import { StatusLegend } from "./StatusLegend";
import { ProjectSummaryPanel } from "./ProjectSummaryPanel";

// Три слоя, все раскрашены по статусу пункта — шевроны, описание выбранного
// этапа, карточки оценки. Читатель — руководитель, не разработчик: путей к
// файлам/тестов/технических деталей здесь нет и в самих данных с backend
// (backend/pko/web/analyses.py::_dashboard_json).
export function Dashboard({ analysis }: { analysis: AnalysisResult }) {
  const [selected, setSelected] = useState<number | null>(analysis.items.length > 0 ? 0 : null);
  const selectedItem = selected !== null ? analysis.items[selected] : null;

  return (
    <div className="flex flex-col gap-6">
      <DashboardHeader meta={analysis.meta} readiness={analysis.readiness} />
      <div className="rounded-xl border border-border bg-card p-6 flex flex-col gap-4">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex flex-col gap-1">
            <h2 className="text-sm font-semibold text-muted-foreground">Этапы проекта</h2>
            <p className="text-xs text-muted-foreground">Нажмите на этап, чтобы посмотреть описание</p>
          </div>
          <StatusLegend />
        </div>
        <div className="journey-row">
          {analysis.items.map((item, i) => (
            <button
              key={i}
              type="button"
              className="journey-chevron-button"
              aria-pressed={selected === i}
              aria-label={`${item.title}: ${item.pct}% готово`}
              onClick={() => setSelected(selected === i ? null : i)}
            >
              <Chevron index={i} title={item.title} color={item.color} pct={item.pct} selected={selected === i} />
            </button>
          ))}
        </div>
        {selectedItem && <StageDescriptionPanel item={selectedItem} />}
        <div className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold text-muted-foreground">Оценка</h2>
          <div className="journey-row items-stretch">
            {analysis.items.map((item, i) => (
              <StageEvaluationCard key={i} item={item} />
            ))}
          </div>
        </div>
      </div>
      {analysis.summary && <ProjectSummaryPanel summary={analysis.summary} />}
    </div>
  );
}
