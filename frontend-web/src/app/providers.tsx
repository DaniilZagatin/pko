"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

export function Providers({ children }: { children: React.ReactNode }) {
  // Один QueryClient на жизнь вкладки — создаётся через useState (не
  // useRef/модульный синглтон), чтобы каждый рендер SSR не переиспользовал
  // один и тот же клиент между разными запросами (стандартная рекомендация
  // TanStack Query для App Router).
  const [client] = useState(() => new QueryClient());
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
