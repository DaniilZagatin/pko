import type { JourneyItem } from "./Dashboard";

// Заменяет собой три отдельные дорожки шаблона («Сделано в 2025» /
// «Инициативы 2026» / «За скоупом Программы в 2026») — по разметке
// пользователя они схлопнуты в одну строку под «Целевой опыт клиента»: одна
// ячейка на пункт плана, с комментарием агента внутри и заливкой фона по
// статусу. Без отдельной подписи слева — визуально это продолжение строки
// «Целевой опыт клиента» над ней, не самостоятельная дорожка.
export function CommentRow({ items }: { items: JourneyItem[] }) {
  return (
    <div className="slide-row">
      <div className="slide-row-label" />
      <div className="slide-row-content">
        {items.map((item, i) => (
          <div key={i} className={`comment-cell comment-cell-${item.color}`}>
            {item.explanation}
          </div>
        ))}
      </div>
    </div>
  );
}
