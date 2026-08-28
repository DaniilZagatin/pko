import type { Metadata } from "next";
import Script from "next/script";
import { Geist, Geist_Mono } from "next/font/google";
import { Providers } from "./providers";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "PKO — оценка готовности проекта",
  description: "Сравнение плана проекта с фактическим состоянием кода",
};

// Ставит .dark на <html> ещё до гидратации (strategy="beforeInteractive" —
// инлайнится в <head>, см. next/script), иначе между отрисовкой светлой темы
// по умолчанию и применением сохранённого выбора мелькала бы вспышка.
const THEME_INIT_SCRIPT = `(function () {
  try {
    var stored = localStorage.getItem("theme");
    var dark = stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.classList.toggle("dark", dark);
  } catch (e) {}
})();`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="ru"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <Script id="theme-init" strategy="beforeInteractive">
          {THEME_INIT_SCRIPT}
        </Script>
        <Providers>
          <header className="flex items-center justify-end border-b border-border px-4 py-2">
            <ThemeToggle />
          </header>
          {children}
        </Providers>
      </body>
    </html>
  );
}
