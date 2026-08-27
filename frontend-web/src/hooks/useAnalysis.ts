"use client";

import { useQuery } from "@tanstack/react-query";
import { getAnalysis } from "@/lib/api";

export function useAnalysis(analysisId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["analysis", analysisId],
    queryFn: () => getAnalysis(analysisId),
    enabled,
  });
}
