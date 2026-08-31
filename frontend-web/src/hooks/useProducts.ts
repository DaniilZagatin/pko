"use client";

import { useQuery } from "@tanstack/react-query";
import { listProducts } from "@/lib/api";

export function useProducts() {
  return useQuery({ queryKey: ["products"], queryFn: listProducts });
}
