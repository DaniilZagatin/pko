"use client";

import { useQuery } from "@tanstack/react-query";
import { getProductSnapshots } from "@/lib/api";

export function useProductSnapshots(productId: string) {
  return useQuery({
    queryKey: ["product-snapshots", productId],
    queryFn: () => getProductSnapshots(productId),
  });
}
