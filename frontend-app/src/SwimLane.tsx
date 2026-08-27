// Дорожка-заглушка под рядом шевронов (как «Целевой опыт клиента» / «Сделано
// в 2025» / … на слайде-образце). Пока чисто визуальное оформление — ячейки
// не приходят из вердиктов агента, это осознанно отложено на следующий шаг
// («сначала фронт, потом учим агента заполнять эти дорожки реальными
// данными»). Число ячеек равно числу пунктов плана, чтобы колонки совпадали
// с рядом шевронов над ним при любом N.
export function SwimLane({ label, sublabel, count }: { label: string; sublabel?: string; count: number }) {
  return (
    <div className="slide-row">
      <div className="slide-row-label">
        {label}
        {sublabel && <div className="slide-row-sublabel">{sublabel}</div>}
      </div>
      <div className="slide-row-content">
        {Array.from({ length: count }).map((_, i) => (
          <div className="swimlane-cell" key={i}>tbd</div>
        ))}
      </div>
    </div>
  );
}
