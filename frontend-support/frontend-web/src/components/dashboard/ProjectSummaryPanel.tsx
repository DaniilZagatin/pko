import type { ComponentType } from "react";
import { AlertTriangle, Flag, Rocket } from "lucide-react";
import type { ProjectSummary } from "@/lib/types";

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

function BulletList({ items, dotColor }: { items: string[]; dotColor: string }) {
  if (items.length === 0) return null;
  return (
    <ul className="flex flex-col gap-2">
      {items.map((text, i) => (
        <li key={i} className="flex items-start gap-2 text-sm text-muted-foreground leading-relaxed">
          <span className="mt-1.5 size-1.5 shrink-0 rounded-full" style={{ background: dotColor }} aria-hidden="true" />
          {text}
        </li>
      ))}
    </ul>
  );
}

// Три колонки вместо одного сплошного абзаца — так и структурирован сам
// вывод от backend (`ProjectSummary`), рендер этой формы не подстраивает и
// не парсит текст, только раскладывает уже готовые поля.
export function ProjectSummaryPanel({ summary }: { summary: ProjectSummary }) {
  return (
    <div className="rounded-xl border border-border bg-card p-6 grid gap-6 md:gap-0 md:grid-cols-3 md:divide-x md:divide-border">
      <SummaryColumn icon={Flag} iconColor="var(--primary)" title="Общий вывод">
        <p className="text-sm text-muted-foreground leading-relaxed">{summary.conclusion}</p>
      </SummaryColumn>
      <SummaryColumn icon={AlertTriangle} iconColor="var(--red)" title="Ключевые риски">
        <BulletList items={summary.risks} dotColor="var(--red)" />
      </SummaryColumn>
      <SummaryColumn icon={Rocket} iconColor="var(--primary)" title="Что реализовать в первую очередь">
        <BulletList items={summary.priorities} dotColor="var(--primary)" />
      </SummaryColumn>
    </div>
  );
}
