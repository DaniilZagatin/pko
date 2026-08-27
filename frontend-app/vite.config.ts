import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Собирает src/ в один самодостаточный dist/index.html (весь JS/CSS инлайн,
// без внешних файлов) — тот же принцип "один файл", что и у остальных
// отчётов PKO. backend/pko/render/progress_report.py читает этот файл и
// вырезает из него содержимое <script>, поэтому имя и расположение важны:
// смена пути требует правки _load_journey_bundle() там же.
export default defineConfig({
  plugins: [react(), tailwindcss(), viteSingleFile()],
  resolve: {
    // "@/..." — тот же алиас, что ожидает CLI shadcn/ui при добавлении
    // компонентов (components.json), без него `npx shadcn add ...` кладёт
    // импорты, которые никуда не резолвятся.
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  build: {
    outDir: "dist",
    assetsInlineLimit: 100000000,
    cssCodeSplit: false,
  },
});
