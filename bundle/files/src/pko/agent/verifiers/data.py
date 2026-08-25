"""Признаки работы с данными: SQL, ORM, файлы, объектные хранилища.

Общее правило перечисленных здесь шаблонов: ищется конструкция, а не слово о
ней. `SELECT` без `FROM`, переменная с именем `delete_mode` или комментарий
«здесь пишем в базу» подтверждением не являются — иначе агент менял бы вердикт
«только чтение» формулировкой.
"""

from __future__ import annotations

import re

PATTERNS: dict[tuple[str, str], re.Pattern[str]] = {
    ("sql", "write"): re.compile(
        r"\b(insert\s+into|update\s+\w+\s+set|delete\s+from"
        r"|drop\s+(table|view|index|schema|database|materialized)"
        r"|truncate\s+table|alter\s+(table|view|schema|role|user|database))",
        re.IGNORECASE,
    ),
    ("sql", "read"): re.compile(r"\bselect\b[\s\S]{0,200}\bfrom\b", re.IGNORECASE),
    # ORM прячет SQL за методами: доказательством считается сам вызов.
    # Общие глаголы (`update`, `create`, `delete`, `commit`) требуют получателя,
    # похожего на доступ к данным: без этого `opts.update(x)` и `repo.commit()`
    # подтверждали бы заявленное изменение данных.
    ("orm", "write"): re.compile(
        r"\bsession\.(add|add_all|merge|delete)\s*\(|\.bulk_(save|insert|update)\w*\s*\("
        r"|\b(session|db|conn|connection|cursor|objects|manager|repo|repository|store)"
        r"\.(update|create|delete|commit|save|execute)\s*\("
        r"|\.objects\.(create|update|delete)\s*\(|\.save\s*\(\s*\)",
        re.IGNORECASE,
    ),
    ("orm", "read"): re.compile(
        r"\.query\s*\(|session\.(get|scalars|execute)\s*\(|\.objects\.(all|filter|get)\s*\("
        r"|select\s*\(\s*\w|\.find(_one|_many)?\s*\(",
    ),
    ("fs", "write"): re.compile(
        r"open\s*\([^)]*[\"'][wax]b?\+?[\"']|\.write_(text|bytes)\s*\(|\bshutil\.(copy|move)\w*\s*\("
        r"|\b\w+\.write\s*\(|os\.(remove|unlink|rename|makedirs|mkdir)\s*\(|\.mkdir\s*\(",
    ),
    ("fs", "read"): re.compile(
        r"open\s*\([^)]*[\"']r b?\+?[\"']|open\s*\([^)]*[\"']rb?[\"']|\.read_(text|bytes)\s*\("
        r"|os\.listdir\s*\(|\.glob\s*\(|\.iterdir\s*\(",
    ),
    ("object_storage", "write"): re.compile(
        r"\.(put_object|upload_file|upload_fileobj|copy_object|delete_object)\s*\(",
    ),
    ("object_storage", "read"): re.compile(
        r"\.(get_object|download_file|download_fileobj|list_objects\w*|head_object)\s*\(",
    ),
    ("nosql", "write"): re.compile(
        r"\.(insert_one|insert_many|update_one|update_many|delete_one|delete_many|replace_one)\s*\("
        r"|\.index\s*\(|\.bulk\s*\(",
    ),
    ("nosql", "read"): re.compile(
        r"\.(find|find_one|aggregate|search|count_documents)\s*\(",
    ),
    # Хранилище состояния тоже направленное: чтение не доказывает запись.
    ("state_store", "write"): re.compile(
        r"\.(set|setex|setnx|hset|mset|expire|incr|decr|rpush|lpush|sadd|delete)\s*\(",
    ),
    ("state_store", "read"): re.compile(
        r"\.(get|mget|hget|hgetall|lrange|smembers|exists|scan)\s*\(",
    ),
    ("state_store", ""): re.compile(r"\bRedis\s*\(|\bStrictRedis\s*\(|\bMemcache\w*\s*\("),
}
