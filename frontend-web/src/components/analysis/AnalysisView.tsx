"use client";

import { useAnalysisEvents } from "@/hooks/useAnalysisEvents";
import { useAnalysis } from "@/hooks/useAnalysis";
import { AnalysisProgress } from "./AnalysisProgress";
import { Dashboard } from "@/components/dashboard/Dashboard";

function ErrorBox({ message, hint }: { message: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-border bg-card p-6">
      <p className="text-destructive font-medium">{message}</p>
      {hint && <p className="text-muted-foreground text-sm mt-1">{hint}</p>}
    </div>
  );
}

// Экран прогресса и dashboard — один роут (app/analysis/[id]/page.tsx), не
// отдельный переход страницы: SSE (`useAnalysisEvents`) переключает состояние
// на READY/ERROR, после чего подтягивается сам результат (`useAnalysis`).
export function AnalysisView({ analysisId }: { analysisId: string }) {
  const { events, done } = useAnalysisEvents(analysisId);
  const { data, error } = useAnalysis(analysisId, done);

  if (!done || !data) {
    return <AnalysisProgress events={events} />;
  }
  if (error) {
    return <ErrorBox message="Не удалось получить результат анализа." hint={String(error)} />;
  }
  if (data.status === "ERROR") {
    return <ErrorBox message={data.message} hint={data.hint} />;
  }
  if (data.status === "PROCESSING") {
    return <AnalysisProgress events={events} />;
  }
  return <Dashboard analysis={data} />;
}
