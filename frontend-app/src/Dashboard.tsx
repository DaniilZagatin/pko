import { Chevron } from "./Chevron";
import { SwimLane } from "./SwimLane";
import { CommentRow } from "./CommentRow";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

// Контракт данных: backend/pko/render/progress_report.py сериализует ровно
// эти поля из ProgressModel.verdicts в window.__JOURNEY_ITEMS__. `label` —
// уже готовый текст статуса (единственный источник — `_STATUS_LABELS` в
// Python), чтобы не заводить вторую копию русских подписей на JS-стороне.
export interface JourneyItem {
  title: string;
  status: string;
  label: string;
  color: string;
  pct: number;
  explanation: string;
}

// Вкладки, бейдж «ОР», заголовок, «Сегмент»/«Сценарий» и все дорожки под
// шевронами кроме самого ряда шевронов — статичное оформление слайда, не
// данные: у нас нет источника ни для сегмента/сценария конкретного
// документа, ни для деления по годам/скоупу. Осознанно оставлено как
// визуальный каркас (см. обсуждение) — реальные данные сюда подключаются
// отдельным следующим шагом.
export function Dashboard({ items }: { items: JourneyItem[] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="slide-dashboard">
      <Tabs defaultValue="как">
        <TabsList className="mb-4 h-auto gap-2 bg-transparent p-0">
          {["зачем", "что", "как"].map((tab) => (
            <TabsTrigger
              key={tab}
              value={tab}
              className="rounded-full border border-border px-4 py-1.5 text-muted-foreground shadow-none data-active:border-foreground data-active:bg-foreground data-active:text-background dark:data-active:bg-foreground"
            >
              {tab}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>

      <div className="slide-header">
        <div className="slide-header-left">
          <span className="slide-badge">ОР</span>
          <div>
            <div className="slide-title">Образ результата: клиентский путь</div>
            <div className="slide-subtitle">в соответствии с видением, декомпозиция на инициативы</div>
          </div>
        </div>
        <div className="slide-header-right">
          <div><b>Сегмент:</b> [указать сегмент]</div>
          <div><b>Сценарий</b>: Клиент + ИИ</div>
        </div>
      </div>

      <div className="slide-row">
        <div className="slide-row-label">Этапы пути</div>
        <div className="slide-row-content journey-row">
          {items.map((item, i) => (
            <Chevron key={i} index={i} title={item.title} color={item.color} pct={item.pct} />
          ))}
        </div>
      </div>

      <SwimLane label="Целевой опыт клиента" count={items.length} />
      <CommentRow items={items} />
    </div>
  );
}
