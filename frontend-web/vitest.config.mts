import path from "node:path";
import { defineConfig } from "vitest/config";

// Только для юнит-тестов чистой логики (chevron-geometry.ts) — не подменяет
// next dev/build. Тот же alias "@/*", что и в tsconfig.json.
export default defineConfig({
  resolve: {
    alias: { "@": path.resolve(import.meta.dirname, "./src") },
  },
});
