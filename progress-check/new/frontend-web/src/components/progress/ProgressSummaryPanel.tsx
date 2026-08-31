import type { ComponentType } from "react";
import { AlertTriangle, Flag, Target } from "lucide-react";
import type { RiskItem, VersionComparison } from "@/lib/types";

const RISK_STATE_LABEL: Record<RiskItem["state"], string> = {
  NEW: "новый", PERSISTING: "сохраняется", RESOLVED: "устранён",
};

function SummaryColumn({
  icon: Icon, iconColor, title, children,
}: {
  icon: ComponentType<{ className?: string }>;
  iconColor: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 md:px-6 md:first:pl-0 md:last:pr-0">
      <div className="flex items-center gap-2.5">
        <span
          className="flex size-8 shrink-0 items-center justify-center rounded-full"
          style={{ background: `color-mix(in srgb, ${iconColor} 15%, transparent)`, color: iconColor }}
        >
          <Icon className="size-4" aria-hidden="true" />
        </span>
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      </div>
      {children}
    </div>
  );
}

// Нижняя панель Progress Mode (план версионирования, §24-27, §29): три
// колонки вместо «Общий вывод/Риски/Приоритеты» текущего состояния — здесь
// это прогресс периода, актуальные (не все исторические) риски и следующий
// фокус. Устранённые риски — отдельный блок ниже, не среди актуальных (§26).
export function ProgressSummaryPanel({ comparison }: { comparison: VersionComparison }) {
  const activeRisks = comparison.current_risks.filter((r) => r.state !== "RESOLVED");
  const resolvedRisks = comparison.current_risks.filter((r) => r.state === "RESOLVED");

  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-xl border border-border bg-card p-6 grid gap-6 md:gap-0 md:grid-cols-3 md:divide-x md:divide-border">
        <SummaryColumn icon={Flag} iconColor="var(--primary)" title="Основной прогресс">
          <p className="text-sm text-muted-foreground leading-relaxed">
            {comparison.progress_summary || "Сводный вывод по периоду пока не сформирован."}
          </p>
        </SummaryColumn>
        <SummaryColumn icon={AlertTriangle} iconColor="var(--red)" title="Актуальные риски">
          {activeRisks.length === 0 ? (
            <p className="text-sm text-muted-foreground">Активных рисков не выявлено.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {activeRisks.map((risk, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-muted-foreground leading-relaxed">
                  <span
                    className="mt-1.5 size-1.5 shrink-0 rounded-full"
                    style={{ background: "var(--red)" }}
                    aria-hidden="true"
                  />
                  <span>{risk.text} <span className="text-xs">({RISK_STATE_LABEL[risk.state]})</span></span>
                </li>
              ))}
            </ul>
          )}
        </SummaryColumn>
        <SummaryColumn icon={Target} iconColor="var(--primary)" title="Следующий фокус">
          {comparison.next_focus.length === 0 ? (
            <p className="text-sm text-muted-foreground">Приоритеты пока не определены.</p>
          ) : (
            <ol className="flex flex-col gap-2 list-decimal list-inside">
              {comparison.next_focus.map((item, i) => (
                <li key={i} className="text-sm text-muted-foreground leading-relaxed">{item}</li>
              ))}
            </ol>
          )}
        </SummaryColumn>
      </div>
      {resolvedRisks.length > 0 && (
        <div className="rounded-xl border border-border bg-card p-6">
          <h3 className="text-sm font-semibold text-foreground mb-3">Что удалось закрыть за период</h3>
          <ul className="flex flex-col gap-2">
            {resolvedRisks.map((risk, i) => (
              <li key={i} className="flex items-start gap-2 text-sm text-muted-foreground leading-relaxed">
                <span style={{ color: "var(--green)" }} aria-hidden="true">✓</span>
                {risk.text}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
