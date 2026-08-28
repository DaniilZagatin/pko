"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

const THEME_EVENT = "pko-theme-change";

// useSyncExternalStore, а не useState+useEffect: .dark на <html> — внешнее
// (не React) состояние, которое к тому же должно совпасть с тем, что синхронно
// выставил theme-init скрипт в layout.tsx ещё до гидратации — getServerSnapshot
// возвращает false (как на сервере), React сам досинхронизирует после монтажа.
function subscribe(callback: () => void) {
  document.addEventListener(THEME_EVENT, callback);
  return () => document.removeEventListener(THEME_EVENT, callback);
}

function getSnapshot() {
  return document.documentElement.classList.contains("dark");
}

function getServerSnapshot() {
  return false;
}

export function ThemeToggle() {
  const isDark = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  function toggle() {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
    document.dispatchEvent(new Event(THEME_EVENT));
  }

  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label={isDark ? "Включить светлую тему" : "Включить тёмную тему"}
    >
      {isDark ? <Sun className="size-4" aria-hidden="true" /> : <Moon className="size-4" aria-hidden="true" />}
    </Button>
  );
}
