import type { StageItem } from "./types";

// Зеркало backend/pko/render/progress_report.py::STATUS_LABELS. Дашборд
// «Текущее состояние» получает label/color готовыми в самом JSON
// (dashboard_json), но `StageDelta` (Progress Mode) несёт только статус —
// цвет и подпись для previous/current считаются на клиенте этой таблицей.
const STATUS_META: Record<StageItem["status"], { label: string; color: StageItem["color"] }> = {
  DONE: { label: "Сделано", color: "green" },
  PARTIAL: { label: "Частично", color: "amber" },
  NOT_STARTED: { label: "Не начато", color: "red" },
  UNCLEAR: { label: "Неясно", color: "purple" },
};

export function statusLabel(status: StageItem["status"] | null): string {
  return status ? STATUS_META[status].label : "—";
}

export function statusColor(status: StageItem["status"] | null): StageItem["color"] {
  return status ? STATUS_META[status].color : "purple";
}
