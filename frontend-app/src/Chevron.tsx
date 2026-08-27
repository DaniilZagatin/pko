import { useEffect, useId, useState } from "react";
import { computeChevronGeometry } from "./chevron-geometry";

// adj пресета "chevron" из самого слайда-образца (см. chevron-geometry.ts) —
// не выдумано, вытащено из XML презентации.
const CHEVRON_ADJ = 37356;

export interface ChevronProps {
  title: string;
  color: string;
  pct: number;
  index?: number;
  width?: number;
  height?: number;
}

export function Chevron({ title, color, pct, index = 0, width = 220, height = 90 }: ChevronProps) {
  const clipId = useId();
  const { x1, points } = computeChevronGeometry(width, height, CHEVRON_ADJ);
  const clamped = Math.min(Math.max(pct, 0), 100);
  const halfH = height / 2;

  // Заливка едет от 0 до целевого % при появлении, не сразу целиком —
  // единственная анимация здесь про смысл (сколько сделано), а не про
  // украшение; на первом кадре рендерим 0, на следующем — реальный процент,
  // чтобы CSS-transition реально сработал (без паузы между кадрами браузер
  // схлопнул бы 0 -> pct в один кадр без анимации).
  const [animatedPct, setAnimatedPct] = useState(0);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setAnimatedPct(clamped));
    return () => cancelAnimationFrame(frame);
  }, [clamped]);

  // Маска заливки — не прямоугольник со срезом по вертикали, а тот же
  // остроконечный скос, что у самой фигуры (x1 — глубина выемки/острия по
  // формуле пресета chevron, см. chevron-geometry.ts): большой блок с
  // остриём справа, растянутый далеко влево (-1000), чтобы никогда не
  // задевать левую выемку. Двигаем его translateX'ом (а не шириной) — так
  // граница заливки идёт под тем же углом, что и скос фигуры, а не прямым
  // вертикальным срезом, и остаётся плавно анимируемой через CSS-transition.
  const maskPoints = `-1000,0 0,0 ${x1},${halfH} 0,${height} -1000,${height}`;
  const maskOffset = -x1 + (animatedPct / 100) * width;

  return (
    <svg
      className="journey-chevron"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`${title}: ${clamped}% готово`}
      style={{ animationDelay: `${index * 70}ms` }}
    >
      <defs>
        <clipPath id={clipId}>
          <polygon
            points={maskPoints}
            className="journey-chevron-fill-mask"
            style={{ transform: `translateX(${maskOffset}px)`, transitionDelay: `${index * 90}ms` }}
          />
        </clipPath>
        <filter id={`${clipId}-shadow`} x="-20%" y="-40%" width="140%" height="220%">
          <feDropShadow dx="0" dy="2" stdDeviation="3" floodColor="#000000" floodOpacity="0.12" />
        </filter>
      </defs>
      <g filter={`url(#${clipId}-shadow)`}>
        <polygon
          points={points}
          fill="var(--surface)"
          stroke={`var(--${color})`}
          strokeWidth={3}
          strokeLinejoin="round"
        />
        {/* Тот же контур и у залитой фигуры сверху — без него на границе
            заливки обводка выглядела бы вдвое тоньше (заливка перекрывала
            бы внутреннюю половину обводки фоновой фигуры под ней). Общий
            путь + один цвет/толщина у обеих — визуально один жирный контур
            на всю фигуру, не два разных. */}
        <polygon
          points={points}
          fill={`var(--${color}-light)`}
          stroke={`var(--${color})`}
          strokeWidth={3}
          strokeLinejoin="round"
          clipPath={`url(#${clipId})`}
        />
      </g>
      <foreignObject x={0} y={0} width={width} height={height}>
        {/* Настоящий HTML-текст: браузер сам переносит строки и, если не
            влезло, аккуратно обрезает многоточием (-webkit-line-clamp) —
            вместо ручного подсчёта символов, как было в Python-версии.
            Центрирование — на внешнем flex-контейнере, а не на самом
            line-clamp-блоке: в Chromium `-webkit-box-pack: center` не
            работает вместе с `-webkit-line-clamp` (клэмп её игнорирует,
            текст всегда прижимался бы к верхнему краю). */}
        <div className="journey-chevron-title">
          <div className="journey-chevron-title-text" title={title}>{title}</div>
        </div>
      </foreignObject>
    </svg>
  );
}
