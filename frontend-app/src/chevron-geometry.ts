// Точная формула пресета PowerPoint prstGeom="chevron" (ECMA-376), взята
// дословно из presetShapeDefinitions.xml (тем же файлом пользуется Apache
// POI). Слайд-образец использует именно этот пресет с adj=37356 — это не
// произвольно нарисованная форма, а воспроизведение реальной геометрии.
//
//   ss = min(w, h)
//   maxAdj = 100000 * w / ss
//   a = clamp(adj, 0, maxAdj)
//   x1 = ss * a / 100000
//   x2 = w - x1
//   path: (0,0) -> (x2,0) -> (w, h/2) -> (x2,h) -> (0,h) -> (x1, h/2) -> close

export interface ChevronGeometry {
  x1: number;
  x2: number;
  points: string;
}

const DEFAULT_ADJ = 50000;

export function computeChevronGeometry(w: number, h: number, adj: number = DEFAULT_ADJ): ChevronGeometry {
  const ss = Math.min(w, h);
  const maxAdj = (100000 * w) / ss;
  const a = Math.min(Math.max(adj, 0), maxAdj);
  const x1 = (ss * a) / 100000;
  const x2 = w - x1;
  const halfH = h / 2;
  const points = `0,0 ${x2},0 ${w},${halfH} ${x2},${h} 0,${h} ${x1},${halfH}`;
  return { x1, x2, points };
}
