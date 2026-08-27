const ITEMS: { color: string; label: string }[] = [
  { color: "green", label: "Сделано" },
  { color: "amber", label: "Частично" },
  { color: "red", label: "Не начато" },
  { color: "purple", label: "Неясно" },
];

export function StatusLegend() {
  return (
    <div className="flex items-center gap-3 flex-wrap text-xs text-muted-foreground">
      {ITEMS.map(({ color, label }) => (
        <span key={color} className="flex items-center gap-2">
          <span className="size-2 rounded-full" style={{ background: `var(--${color})` }} />
          {label}
        </span>
      ))}
    </div>
  );
}
