"""Признаки траектории исполнения: граф, инструменты агента, вызовы моделей.

Общие слова (`node`, `step`, стрелка `->`) сюда не входят: первое встречается
в комментарии и в имени переменной, второе есть в каждой аннотации типа, так
что проверка на них была бы пустой.
"""

from __future__ import annotations

import re

PATTERNS: dict[tuple[str, str], re.Pattern[str]] = {
    # Общий шаблон механизма — объявление графа и его узлов. Переходы сюда не
    # входят намеренно: `add_edge(` доказывает связь между узлами, но не то,
    # что указанная строка сама является узлом, а `GRAPH_NODE` попадает в
    # `policies.steps` и способен в одиночку перевести CHK-AP-001 в PASS.
    ("graph", ""): re.compile(
        r"\badd_node\s*\(|\bStateGraph\s*\(|\bGraph\s*\(|@\w*(task|step|node)\b",
        re.IGNORECASE,
    ),
    ("graph", "transition"): re.compile(
        r"\badd_edge\s*\(|\badd_conditional_edges\s*\(|\bset_entry_point\s*\("
        r"|\bset_finish_point\s*\(",
    ),
    # Инструмент агента объявляется декоратором или регистрацией в списке.
    ("agent_tool", ""): re.compile(
        r"@\w*tool\b|\btools\s*=\s*\[|\bTool\s*\(|\bStructuredTool\b|\bbind_tools\s*\(",
        re.IGNORECASE,
    ),
    ("llm", "call"): re.compile(
        r"\.(create|parse|invoke|ainvoke|stream|complete|chat)\s*\("
        r"|chat\.completions|ChatCompletion|\bmessages\s*=\s*\[",
        re.IGNORECASE,
    ),
}
