"use client";

import { useState } from "react";
import { useCompare } from "@/hooks/useCompare";
import type { SnapshotSummary } from "@/lib/types";
import { ProgressChevron } from "./ProgressChevron";
import { ProgressSummaryPanel } from "./ProgressSummaryPanel";
import { ReadinessDeltaHeader } from "./ReadinessDeltaHeader";
import { StageDiffPanel } from "./StageDiffPanel";
import { VersionSlider } from "./VersionSlider";

// Владеет ProgressViewState (план версионирования, §31): snapshots приходят
// от родителя (ProductView уже их загрузил для переключателя режимов),
// fromIndex/toIndex/selectedStageId — состояние этого экрана.
export function ProgressDashboard({
  productId, snapshots,
}: { productId: string; snapshots: SnapshotSummary[] }) {
  const [fromIndex, setFromIndex] = useState(() => Math.max(0, snapshots.length - 2));
  const [toIndex, setToIndex] = useState(() => Math.max(0, snapshots.length - 1));
  const [selectedStageId, setSelectedStageId] = useState<string | null>(null);

  // Новый snapshot появился (пользователь только что прогнал ещё одну
  // проверку и вернулся сюда) — сброс на пару "предпоследняя -> последняя"
  // прямо во время рендера (не в useEffect: React рекомендует именно так
  // подстраивать состояние под изменившийся проп, без лишнего лишнего цикла
  // рендер -> эффект -> ещё рендер).
  const [knownCount, setKnownCount] = useState(snapshots.length);
  if (snapshots.length !== knownCount) {
    setKnownCount(snapshots.length);
    setFromIndex(Math.max(0, snapshots.length - 2));
    setToIndex(Math.max(0, snapshots.length - 1));
    setSelectedStageId(null);
  }

  // `useCompare` вызывается безусловно (до ранних return ниже) — правила
  // хуков; при <2 снимков сравнивать нечего, поэтому просто `enabled: false`.
  const from = snapshots[fromIndex];
  const to = snapshots[toIndex];
  const { data: comparison, isLoading } = useCompare(
    productId, from?.id ?? "", to?.id ?? "", snapshots.length >= 2
  );

  if (snapshots.length < 2) {
    return (
      <div className="rounded-xl border border-border bg-card p-8 text-center flex flex-col gap-2">
        <p className="text-sm font-medium text-foreground">
          История прогресса появится после следующей проверки продукта.
        </p>
        <p className="text-sm text-muted-foreground">
          После повторной загрузки материалов здесь можно будет сравнить готовность,
          изменения по этапам и динамику рисков.
        </p>
      </div>
    );
  }

  const selectedDelta = comparison?.stage_deltas.find(
    (d) => d.canonical_stage_id === selectedStageId
  ) ?? null;

  function handleRangeChange(nextFrom: number, nextTo: number) {
    setFromIndex(nextFrom);
    setToIndex(nextTo);
    setSelectedStageId(null);
  }

  return (
    <div className="flex flex-col gap-6">
      <ReadinessDeltaHeader from={from} to={to} comparison={comparison} />
      <VersionSlider
        snapshots={snapshots}
        fromIndex={fromIndex}
        toIndex={toIndex}
        onChange={handleRangeChange}
      />
      <div className="rounded-xl border border-border bg-card p-6 flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <h2 className="text-sm font-semibold text-muted-foreground">Динамика этапов</h2>
          <p className="text-xs text-muted-foreground">Нажмите на этап, чтобы посмотреть, что изменилось</p>
        </div>
        {isLoading || !comparison ? (
          <p className="text-sm text-muted-foreground">Считаем разницу…</p>
        ) : (
          <>
            <div className="journey-row">
              {comparison.stage_deltas.map((delta, i) => (
                <button
                  key={delta.canonical_stage_id}
                  type="button"
                  className="journey-chevron-button"
                  aria-pressed={selectedStageId === delta.canonical_stage_id}
                  aria-label={`${delta.title}: ${delta.change_type}`}
                  onClick={() => setSelectedStageId(
                    selectedStageId === delta.canonical_stage_id ? null : delta.canonical_stage_id
                  )}
                >
                  <ProgressChevron
                    delta={delta} index={i} selected={selectedStageId === delta.canonical_stage_id}
                  />
                </button>
              ))}
            </div>
            {selectedDelta && <StageDiffPanel delta={selectedDelta} from={from} to={to} />}
          </>
        )}
      </div>
      {comparison && <ProgressSummaryPanel comparison={comparison} />}
    </div>
  );
}
