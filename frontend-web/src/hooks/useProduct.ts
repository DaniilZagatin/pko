"use client";

import { useQuery } from "@tanstack/react-query";
import { getProduct } from "@/lib/api";

export function useProduct(productId: string) {
  return useQuery({ queryKey: ["product", productId], queryFn: () => getProduct(productId) });
}
