"use client";

import type { SnapshotSummary } from "@/lib/types";

function formatShortDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
}

function formatFullDate(iso: string): string {
  return new Date(iso).toLocaleDateString("ru-RU", { day: "2-digit", month: "long", year: "numeric" });
}

export interface VersionSliderProps {
  snapshots: SnapshotSummary[];
  fromIndex: number;
  toIndex: number;
  onChange: (fromIndex: number, toIndex: number) => void;
}

// Dual-range snap-to-version (план версионирования, §15/16): ползунки
// индексируют массив снимков, а не дату — между реальными проверками
// позиций не бывает. Два <input type="range"> наложены друг на друга;
// CSS (.version-slider-input в globals.css) делает трек прозрачным для
// кликов, интерактивен только сам бегунок — иначе верхний по DOM-порядку
// input перехватывал бы весь трек и второй бегунок было бы почти
// невозможно двигать.
export function VersionSlider({ snapshots, fromIndex, toIndex, onChange }: VersionSliderProps) {
  const max = snapshots.length - 1;
  // 10+ снимков — не подписывать все даты подряд (план версионирования,
  // §35), оставить точки и полные даты по hover; отмечать всегда только
  // выбранную пару и крайние точки истории.
  const crowded = snapshots.length > 8;

  function handleFrom(value: number) {
    onChange(Math.min(value, toIndex - 1), toIndex);
  }
  function handleTo(value: number) {
    onChange(fromIndex, Math.max(value, fromIndex + 1));
  }

  return (
    <div className="rounded-xl border border-border bg-card p-6 flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-sm font-semibold text-muted-foreground">История проверок</h2>
        <div className="flex gap-2">
          <ShortcutButton label="С предыдущей" onClick={() => onChange(Math.max(0, max - 1), max)} />
          <ShortcutButton
            label="За месяц"
            onClick={() => onChange(nearestMonthAgoIndex(snapshots, max), max)}
          />
          <ShortcutButton label="С первой" onClick={() => onChange(0, max)} />
        </div>
      </div>
      <div className="relative h-6 flex items-center">
        <div className="absolute left-2 right-2 h-1 rounded-full bg-muted" aria-hidden="true" />
        <div
          className="absolute h-1 rounded-full bg-primary"
          style={{
            left: `calc(${(fromIndex / max) * 100}% * 0.96 + 0.5rem)`,
            right: `calc(${(1 - toIndex / max) * 100}% * 0.96 + 0.5rem)`,
          }}
          aria-hidden="true"
        />
        <input
          type="range" min={0} max={max} step={1} value={fromIndex}
          onChange={(e) => handleFrom(Number(e.target.value))}
          className="version-slider-input"
          aria-label="Начальная проверка"
        />
        <input
          type="range" min={0} max={max} step={1} value={toIndex}
          onChange={(e) => handleTo(Number(e.target.value))}
          className="version-slider-input"
          aria-label="Конечная проверка"
        />
      </div>
      <div className="flex justify-between text-xs text-muted-foreground">
        {snapshots.map((snapshot, i) => {
          const isEndpoint = i === fromIndex || i === toIndex;
          const showLabel = !crowded || isEndpoint || i === 0 || i === max;
          return (
            <span
              key={snapshot.id}
              className={isEndpoint ? "font-semibold text-foreground" : ""}
              title={
                `${formatFullDate(snapshot.created_at)} · версия ${snapshot.version_number} · ` +
                `${Math.round(snapshot.overall_readiness * 100)}%`
              }
            >
              {showLabel ? formatShortDate(snapshot.created_at) : "·"}
            </span>
          );
        })}
      </div>
    </div>
  );
}

function ShortcutButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md border border-border px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
    >
      {label}
    </button>
  );
}

function nearestMonthAgoIndex(snapshots: SnapshotSummary[], toIndex: number): number {
  const toDate = new Date(snapshots[toIndex].created_at).getTime();
  const targetDate = toDate - 30 * 24 * 60 * 60 * 1000;
  let best = 0;
  let bestDiff = Infinity;
  for (let i = 0; i < toIndex; i++) {
    const diff = Math.abs(new Date(snapshots[i].created_at).getTime() - targetDate);
    if (diff < bestDiff) {
      best = i;
      bestDiff = diff;
    }
  }
  return best;
}
