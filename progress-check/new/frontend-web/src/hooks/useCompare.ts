"use client";

import { useQuery } from "@tanstack/react-query";
import { compareSnapshots } from "@/lib/api";

export function useCompare(productId: string, fromId: string, toId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["compare", productId, fromId, toId],
    queryFn: () => compareSnapshots(productId, fromId, toId),
    enabled,
  });
}
