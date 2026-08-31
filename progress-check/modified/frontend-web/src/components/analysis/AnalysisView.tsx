"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
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
  const router = useRouter();
  const { events, done } = useAnalysisEvents(analysisId);
  const { data, error } = useAnalysis(analysisId, done);

  // Анализ, привязанный к продукту (`product_id` в ответе — см.
  // backend/pko/web/analyses.py::_execute), не задерживается на разовом
  // dashboard: там уже есть история, есть смысл сразу открыть страницу
  // продукта с «Текущим состоянием» и «Прогрессом», а не показывать тот же
  // dashboard здесь и заставлять переходить туда вручную.
  useEffect(() => {
    if (data?.status === "READY" && data.product_id) {
      router.replace(`/products/${data.product_id}`);
    }
  }, [data, router]);

  if (!done || !data) {
    return <AnalysisProgress events={events} />;
  }
  if (error) {
    return <ErrorBox message="Не удалось получить результат анализа." hint={String(error)} />;
  }
  if (data.status === "ERROR") {
    return <ErrorBox message={data.message} hint={data.hint} />;
  }
  if (data.status === "PROCESSING" || data.product_id) {
    return <AnalysisProgress events={events} />;
  }
  return <Dashboard analysis={data} />;
}
