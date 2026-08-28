import type { StageItem } from "@/lib/types";
import { StatusDot } from "./StatusDot";

export function StageEvaluationCard({ item }: { item: StageItem }) {
  return (
    <div
      className={`stage-cell stage-eval-card stage-cell-${item.color} flex-1 min-w-0 flex flex-col gap-2`}
      style={{ borderColor: `color-mix(in srgb, var(--${item.color}) 35%, transparent)` }}
    >
      <div className="flex items-center gap-2 text-sm font-semibold" style={{ color: `var(--${item.color})` }}>
        <StatusDot color={item.color} size={10} />
        {item.label}
      </div>
      <p className="text-sm text-foreground/80 leading-relaxed">{item.explanation}</p>
    </div>
  );
}
