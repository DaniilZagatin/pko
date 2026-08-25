"""Признаки точек входа: HTTP, интерфейс, командная строка, расписание.

Точкой входа считается объявление, а не упоминание. Слово `route` в имени
переменной или в комментарии подтверждением не является: раньше именно на
таких словах агент мог объявить точку входа там, где её нет.
"""

from __future__ import annotations

import re

PATTERNS: dict[tuple[str, str], re.Pattern[str]] = {
    ("http_server", ""): re.compile(
        r"@\w*\.?(get|post|put|patch|delete)\s*\(|\badd_url_rule\s*\("
        r"|\b(app|router|api|bp|blueprint)\.route\s*\(|\binclude_router\s*\("
        r"|\b(path|re_path|url)\s*\(\s*[\"']|\brouter\.\w+\s*\(\s*[\"']/"
        r"|\bapp\.(use|listen)\s*\(",
    ),
    ("http_client", "call"): re.compile(
        r"\brequests\.(get|post|put|patch|delete)\s*\(|\bhttpx\.\w+\s*\("
        r"|\bfetch\s*\(|\baxios\.\w+\s*\(|\burlopen\s*\(|\bclient\.(get|post)\s*\(",
    ),
    # Интерфейс: обработчик события, а не любая функция с именем handler.
    ("ui_event", ""): re.compile(
        r"\bon[A-Z]\w*\s*=|\baddEventListener\s*\(|\bv-on:|\b@click\b"
        r"|\bst\.(button|form_submit_button|selectbox|text_input|file_uploader)\s*\("
        r"|\buseEffect\s*\(|\bdispatch\s*\(|\bonSubmit\b",
    ),
    ("cli", ""): re.compile(
        r"\badd_argument\s*\(|\bArgumentParser\s*\(|\badd_parser\s*\(|@click\.\w+"
        r"|@app\.command\b|\bsys\.argv\b|\[project\.scripts\]",
    ),
    ("cron", ""): re.compile(
        r"\bcrontab\b|\bschedule\.\w+\s*\(|\bCronTrigger\b|\badd_job\s*\("
        r"|\bbeat_schedule\b|\bschedule_interval\b|\bDAG\s*\(",
        re.IGNORECASE,
    ),
    ("webhook", ""): re.compile(
        r"\bwebhook\w*\s*=|\bWebhook\w*\s*\(|@\w*\.?post\s*\(\s*[\"'][^\"']*hook",
        re.IGNORECASE,
    ),
}
