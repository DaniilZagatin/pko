## Пак: агент и языковые модели

В репозитории найден граф исполнения или обращение к языковой модели.

Где искать: `StateGraph(`, `add_node(`, `add_edge(`, `add_conditional_edges(`,
объявления инструментов (`@tool`, `tools=[`, `bind_tools(`), вызовы моделей
(`chat.completions.create`, `.invoke(`, `.stream(`).

Как размечать:

- узел графа — `STEP` / `graph`; переход — `STATE` / `transition` / `graph`;
- инструмент агента — `COMPONENT` / `call` / `agent_tool`;
- обращение к модели — `EFFECT` / `call` / `llm`;
- `max_tokens`, `max_iterations`, таймаут вызова — `CONTROL` / `limit`.

Осторожно: у агента траектория часто определяется в рантайме решением модели.
Если порядок шагов не выводится из кода, перечисли этапы в
`process_trajectory` — это интерпретация, и в отчёте она так и будет помечена.
