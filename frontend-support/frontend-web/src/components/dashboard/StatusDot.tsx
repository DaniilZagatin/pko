export function StatusDot({ color, size = 8 }: { color: string; size?: number }) {
  return (
    <span
      className="inline-block shrink-0 rounded-full"
      style={{ width: size, height: size, background: `var(--${color})` }}
      aria-hidden="true"
    />
  );
}
