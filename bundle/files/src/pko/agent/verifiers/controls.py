"""Признаки ограничений исполнения: числовые лимиты и явные перечни.

Ограничение попадает в проверку Gate `CHK-GRD-002`, поэтому подтверждением
считается заданное значение, а не упоминание слова «таймаут» в тексте.
"""

from __future__ import annotations

import re

PATTERNS: dict[tuple[str, str], re.Pattern[str]] = {
    # Имя ограничения совпадает целиком, а не подстрокой внутри чужого
    # идентификатора: `generated_at = 1` содержит «rate» и раньше подтверждало
    # числовой лимит, наполняя CHK-GRD-002 случайным присваиванием.
    ("limit", ""): re.compile(
        r"(?<![A-Za-z0-9_])[a-z0-9_]*"
        r"(timeout|max_[a-z0-9]+|limit|retries|retry|ttl|rate_limit"
        r"|batch_size|page_size|chunk_size)"
        r"[a-z0-9_]*\s*[:=]\s*(\d|\w+\(\s*\d)",
        re.IGNORECASE,
    ),
    ("allowlist", ""): re.compile(
        r"(?<![A-Za-z0-9_])"
        r"([a-z0-9_]*allowed[a-z0-9_]*|allow_?list|white_?list|permitted[a-z0-9_]*)"
        r"\s*[:=]\s*[\[({]",
        re.IGNORECASE,
    ),
}
