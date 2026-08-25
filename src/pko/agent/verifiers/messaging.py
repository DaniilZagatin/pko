"""Признаки обмена сообщениями: очереди, брокеры, фоновые задачи.

Механизм направленный: отправка и приём — противоположные конструкции.
Поэтому у обработчика (`serve`, `handle`) свой шаблон, а не общий с отправкой:
иначе вызов `send(` доказывал бы существование потребителя, которого в коде
нет, и точка входа попадала бы в проверку траектории на пустом месте.
"""

from __future__ import annotations

import re

_CONSUMER = re.compile(
    r"\.(consume|subscribe|poll|receive_message\w*|basic_consume|listen)\s*\("
    r"|@\w*\.?(task|consumer|listener)\b|\bKafkaConsumer\s*\(",
    re.IGNORECASE,
)

PATTERNS: dict[tuple[str, str], re.Pattern[str]] = {
    ("queue", "serve"): _CONSUMER,
    ("queue", "handle"): _CONSUMER,
    ("queue", "emit"): re.compile(
        r"\.(send|publish|produce|send_message|basic_publish|enqueue)\s*\("
        r"|\.delay\s*\(|\.apply_async\s*\(",
    ),
    ("queue", "read"): _CONSUMER,
    ("queue", ""): re.compile(
        r"\bKafka\w*\s*\(|\bQueue\s*\(|\bCelery\s*\(|@\w*\.?task\b|\bexchange\s*=",
        re.IGNORECASE,
    ),
}
