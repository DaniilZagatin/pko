"use client";

import { useState } from "react";
import { Dashboard } from "@/components/dashboard/Dashboard";
import { ProgressDashboard } from "@/components/progress/ProgressDashboard";
import { useProduct } from "@/hooks/useProduct";
import { useProductSnapshots } from "@/hooks/useProductSnapshots";
import { useSnapshotDashboard } from "@/hooks/useSnapshotDashboard";

type Mode = "current" | "progress";

function ModeSwitch({ mode, onChange }: { mode: Mode; onChange: (mode: Mode) => void }) {
  const tabs: { key: Mode; label: string }[] = [
    { key: "current", label: "Текущее состояние" },
    { key: "progress", label: "Прогресс" },
  ];
  return (
    <div className="inline-flex rounded-lg border border-border bg-muted p-1 text-sm">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          aria-pressed={mode === tab.key}
          onClick={() => onChange(tab.key)}
          className={`rounded-md px-3 py-1.5 font-medium transition-colors ${
            mode === tab.key
              ? "bg-card text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

// Хаб продукта (план версионирования, §11): переключатель «Текущее
// состояние / Прогресс» поверх одной и той же истории снимков. «Текущее
// состояние» переиспользует уже готовый Dashboard на данных последнего
// snapshot — с точки зрения этого компонента разницы с разовым анализом нет.
export function ProductView({ productId }: { productId: string }) {
  const [mode, setMode] = useState<Mode>("current");
  const { data: product, isLoading: productLoading } = useProduct(productId);
  const { data: snapshots, isLoading: snapshotsLoading } = useProductSnapshots(productId);
  const latest = snapshots?.[snapshots.length - 1];
  const { data: dashboard } = useSnapshotDashboard(productId, latest?.id ?? "", Boolean(latest));

  if (productLoading || snapshotsLoading || !product || !snapshots) {
    return <p className="text-sm text-muted-foreground">Загрузка…</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <h1 className="text-lg font-semibold">{product.name}</h1>
        <ModeSwitch mode={mode} onChange={setMode} />
      </div>
      {mode === "current" ? (
        dashboard ? (
          <Dashboard analysis={{ ...dashboard, status: "READY" }} />
        ) : (
          <p className="text-sm text-muted-foreground">Загрузка…</p>
        )
      ) : (
        <ProgressDashboard productId={productId} snapshots={snapshots} />
      )}
    </div>
  );
}
