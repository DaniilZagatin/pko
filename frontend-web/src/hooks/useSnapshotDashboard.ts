"use client";

import { useQuery } from "@tanstack/react-query";
import { getSnapshotDashboard } from "@/lib/api";

export function useSnapshotDashboard(productId: string, snapshotId: string, enabled: boolean) {
  return useQuery({
    queryKey: ["snapshot-dashboard", productId, snapshotId],
    queryFn: () => getSnapshotDashboard(productId, snapshotId),
    enabled,
  });
}
