#!/bin/bash
# Создаёт маленький git-репозиторий, на котором проверяется весь конвейер PKO.
# Две версии: первая — только API и настройки, вторая — граф, инструменты,
# внешние системы, ограничения и подтверждённое бизнес-намерение.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO="$ROOT/fixtures/mini_repo"

rm -rf "$REPO"
mkdir -p "$REPO/backend/src/api/v1" "$REPO/backend/src/config" "$REPO/backend/tests"
cd "$REPO"

git init -q -b master
git config user.email "pko@example.local"
git config user.name "PKO Fixture"

# --- версия 1: минимальный сервис -----------------------------------------
cat > backend/pyproject.toml <<'EOF'
[project]
name = "mini-agent"
version = "0.1.0"
dependencies = ["fastapi==0.110.0", "pydantic-settings==2.2.1"]
EOF

cat > backend/src/api/v1/router.py <<'EOF'
from fastapi import APIRouter

router = APIRouter(prefix="/api/v1")


@router.post("/tasks")
async def start_task(payload: dict) -> dict:
    """Поставить задачу в обработку."""
    return {"id": "t-1"}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    """Получить состояние задачи."""
    return {"id": task_id, "status": "RUNNING"}
EOF

cat > backend/src/config/settings.py <<'EOF'
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_port: int = 8080
    db_max_rows: int = 500
    sql_timeout: int = 30
    llm_api_key: str = "секрет-не-должен-попасть-в-отчёт"
    allowed_tables: list[str] = ["hr_headcount", "hr_positions", "hr_kpi"]


settings = Settings()
EOF

git add -A
git commit -qm "Первая версия: API задач и настройки"

# --- версия 2: граф, инструменты, память, ограничения ---------------------
mkdir -p "$REPO/backend/src/agent" "$REPO/backend/src/memory" "$REPO/backend/src/db_tools"

cat > backend/src/agent/graph.py <<'EOF'
from langgraph.graph import StateGraph


def build_graph():
    builder = StateGraph(dict)
    builder.add_node("plan", plan_step)
    builder.add_node("search_schema", search_schema_step)
    builder.add_node("run_sql", run_sql_step)
    builder.add_node("synthesize", synthesize_step)
    builder.add_edge("plan", "search_schema")
    builder.add_edge("search_schema", "run_sql")
    builder.add_edge("run_sql", "synthesize")
    return builder


def plan_step(state):
    return state


def search_schema_step(state):
    return state


def run_sql_step(state):
    return state


def synthesize_step(state):
    return state
EOF

cat > backend/src/agent/tools.py <<'EOF'
def search_schema(query: str) -> list[str]:
    """Найти подходящие таблицы по описанию."""
    return []


def run_query(sql: str, timeout: int = 60, max_rows: int = 10000) -> list[dict]:
    """Выполнить SQL-запрос на чтение."""
    return []


def save_observation(text: str) -> None:
    """Сохранить наблюдение в долговременную память."""
    return None
EOF

cat > backend/src/db_tools/executor.py <<'EOF'
import sqlalchemy

QUERY_TEMPLATE = "SELECT employee_id, department FROM hr_headcount WHERE dt = :dt"

SQL_TIMEOUT_SECONDS = 60
MAX_RETRIES = 2


def execute(sql: str):
    """Выполнить запрос к аналитической базе."""
    return []
EOF

cat > backend/src/memory/s3_store.py <<'EOF'
import boto3

UPLOAD_TIMEOUT = 15


def save(key: str, payload: bytes) -> None:
    """Положить наблюдение в объектное хранилище."""
    client = boto3.client("s3")
    client.put_object(Bucket="observations", Key=key, Body=payload)
EOF

cat > backend/tests/test_sql_guard.py <<'EOF'
def test_forbidden_update_is_rejected():
    """Изменяющий запрос должен быть отклонён."""
    assert True


def test_timeout_limit_applied():
    assert True
EOF

cat > business_intent.yaml <<'EOF'
confirmed_need_id: NEED-HR-001
need_name: Аналитика корпоративных данных по запросу на естественном языке
business_meaning: Сотрудник получает аналитический ответ по данным без посредников
client: HR-аналитик, руководитель подразделения
business_owner: Иванова А.А., владелец продукта аналитики
journey_purpose: Ответ на аналитический вопрос по корпоративным данным
initial_state: У пользователя есть вопрос и нет ответа
target_state: Пользователь получил проверяемый аналитический ответ
success_criteria: Ответ содержит числа из базы и ссылку на использованные таблицы
requested_mode: CONFIRM
maturity: pilot
consequence: low
scale: local
external_effects: none
decision_boundary: END_TO_END_PROCESS
in_scope: Чтение данных, поиск схем, синтез ответа
out_of_scope: Изменение данных, внешние коммуникации
forbidden_effects: Изменение данных, внешние коммуникации
EOF

# Файл переписывается целиком, как и остальные в этом скрипте: `sed -i ''` работает
# только в BSD-варианте (macOS), а в GNU sed пустая строка съедается как выражение,
# и скрипт падает под `set -e` до второго коммита.
cat > backend/src/config/settings.py <<'EOF'
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_port: int = 8000
    db_max_rows: int = 10000
    sql_timeout: int = 30
    llm_api_key: str = "секрет-не-должен-попасть-в-отчёт"
    allowed_tables: list[str] = ["hr_headcount", "hr_positions", "hr_kpi"]


settings = Settings()
EOF

cat >> backend/pyproject.toml <<'EOF'

[project.optional-dependencies]
agent = ["langgraph==0.5.3", "boto3==1.34.0", "sqlalchemy==2.0.29"]
EOF

git add -A
git commit -qm "Pull request #12: RCA-граф, инструменты агента, память в S3 и ограничения"

# --- отдельная ветка с собственной базовой версией -------------------------
# Нужна, чтобы проверить: выбор первой версии считается по анализируемой ветке,
# а не по HEAD репозитория. Первый коммит ветки кода не содержит вовсе.
git checkout -q --orphan alt
git rm -rq --cached . >/dev/null 2>&1 || true
rm -rf backend business_intent.yaml

cat > README.md <<'EOF'
# Альтернативная ветка

В первом коммите этой ветки прикладного кода нет.
EOF
git add -A
git commit -qm "Ветка alt: только описание, кода нет"

mkdir -p alt
cat > alt/service.py <<'EOF'
def handle(request: dict) -> dict:
    """Обработать запрос альтернативной ветки."""
    return {"ok": True}
EOF
git add -A
git commit -qm "Ветка alt: добавлен первый прикладной код"

# Третий коммит нужен, чтобы первая версия ветки не совпадала с её HEAD.
cat > alt/service.py <<'EOF'
def handle(request: dict, timeout: int = 5) -> dict:
    """Обработать запрос альтернативной ветки."""
    return {"ok": True, "timeout": timeout}
EOF
git add -A
git commit -qm "Ветка alt: добавлен таймаут обработки"

git checkout -q master

echo "Фикстура готова: $REPO"
git --no-pager log --oneline
echo "ветки:"
git --no-pager branch
