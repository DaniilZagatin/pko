import { HelpCircle } from "lucide-react";
import type { StageItem } from "@/lib/types";

// Единственная панель под рядом шевронов — не по одной на этап (как раньше):
// показывает описание только выбранного этапа, Dashboard решает какого по клику.
export function StageDescriptionPanel({ item }: { item: StageItem }) {
  return (
    <div
      className="rounded-xl border-2 bg-card p-5 flex gap-4 items-start"
      style={{ borderColor: `var(--${item.color})` }}
    >
      <div
        className="flex size-11 shrink-0 items-center justify-center rounded-full"
        style={{ background: `var(--${item.color}-light)`, color: `var(--${item.color})` }}
      >
        <HelpCircle className="size-5" aria-hidden="true" />
      </div>
      <div className="flex flex-col gap-1 pt-0.5">
        <h3 className="text-sm font-semibold text-foreground">Описание этапа</h3>
        <p className="text-sm text-muted-foreground leading-relaxed">{item.description}</p>
      </div>
    </div>
  );
}
