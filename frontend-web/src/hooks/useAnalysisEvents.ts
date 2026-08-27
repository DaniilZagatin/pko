"use client";

import { useEffect, useState } from "react";
import type { AnalysisEvent } from "@/lib/types";

// GET /api/analyses/{id}/events (SSE) — backend/pko/web/app.py::stream_analysis_events.
// `EventSource` переподключается сам при обрыве сети; на этот случай backend
// уже умеет сразу отдать терминальное событие, если задача успела
// завершиться между переподключениями (см. докстринг того эндпоинта) —
// здесь достаточно просто открыть соединение и слушать.
export function useAnalysisEvents(analysisId: string) {
  const [events, setEvents] = useState<AnalysisEvent[]>([]);
  const [done, setDone] = useState(false);

  useEffect(() => {
    // Не сбрасываем состояние здесь явно (setState синхронно в эффекте —
    // лишний ререндер): если `analysisId` меняется, вызывающий компонент
    // должен размонтировать/пересоздать это дерево через `key={analysisId}`
    // (см. app/analysis/[id]/page.tsx) — тогда `useState` ниже и так
    // стартует с чистого листа.
    const source = new EventSource(`/api/analyses/${analysisId}/events`);
    source.onmessage = (msg) => {
      const event = JSON.parse(msg.data) as AnalysisEvent;
      setEvents((prev) => [...prev, event]);
      if (event.type === "analysis_ready" || event.type === "error") {
        setDone(true);
        source.close();
      }
    };
    return () => source.close();
  }, [analysisId]);

  return { events, done };
}
