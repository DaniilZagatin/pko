export function StageComment({ text, color }: { text: string; color: string }) {
  return <div className={`stage-cell stage-cell-${color}`}>{text}</div>;
}
