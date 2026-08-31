import { ArrowDown, ArrowRight, ArrowUp, Minus, Plus, type LucideIcon } from "lucide-react";
import { Chevron } from "@/components/dashboard/Chevron";
import { statusColor } from "@/lib/status";
import type { StageDelta } from "@/lib/types";

// Семантика (план версионирования, §20): цвет шеврона — состояние в TO
// snapshot, стрелка/значок под ним — изменение FROM -> TO.
const CHANGE_META: Record<StageDelta["change_type"], { icon: LucideIcon; label: string }> = {
  IMPROVED: { icon: ArrowUp, label: "Улучшилось" },
  UNCHANGED: { icon: ArrowRight, label: "Без изменений" },
  REGRESSED: { icon: ArrowDown, label: "Ухудшилось" },
  ADDED: { icon: Plus, label: "Новый этап" },
  REMOVED: { icon: Minus, label: "Убран из плана" },
};

export function ProgressChevron({
  delta, index = 0, selected = false,
}: { delta: StageDelta; index?: number; selected?: boolean }) {
  const color = statusColor(delta.current_status ?? delta.previous_status);
  const pct = delta.current_readiness ?? 0;
  const { icon: Icon, label } = CHANGE_META[delta.change_type];

  // Без обёртки flex-col вокруг Chevron: у `.journey-chevron` (globals.css)
  // зашит `flex: 1 1 0`, рассчитанный на прямого родителя-flex-row
  // (`.journey-row`) — второй flex-контейнер (колонка) вокруг него
  // схлопывает SVG до нулевой высоты (flex-basis: 0 по главной оси колонки
  // при auto-высоте родителя). Подпись — обычный блочный элемент под ним,
  // не сосед по общему flex.
  return (
    <div className="flex-1 min-w-0">
      <Chevron title={delta.title} color={color} pct={pct} index={index} selected={selected} />
      <div className="mt-1.5 flex items-center justify-center gap-1 text-xs text-muted-foreground" title={label}>
        <Icon className="size-3.5 shrink-0" aria-hidden="true" />
        {label}
      </div>
    </div>
  );
}
