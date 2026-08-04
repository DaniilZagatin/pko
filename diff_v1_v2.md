# Diff: Версия 1 → prod-like (v2)

> **Стандарт:** Автономный процесс v1.1  
> **Профиль:** BASIC  
> **Дата:** 2026-07-27

---

## Общая оценка прогресса

Проект прошёл значительную эволюцию от **монолитного FastAPI + LangGraph агента** (v1) к **модульной архитектуре с RCA, памятью, streaming и визуализацией** (prod-like). Ниже — пообъектный diff.

---

## 1. Потребность клиента (NEED-FIZARUM-001)

| Аспект | v1 | prod-like (v2) | Diff |
|---|---|---|---|
| **Бизнес-намерение** | NL-вопрос → аналитический ответ | То же + RCA | + RCA-аналитика |
| **Клиент** | HR-аналитик, HR BP, руководитель | То же | — |
| **Канал** | Web-чат, REST API | То же (Next.js) | — |
| **Режим** | CONFIRM | CONFIRM | — |
| **Допустимые КП** | Вопрос-ответ | Вопрос-ответ + RCA | + RCA путь |
| **Метрики** | Не формализованы | Успех > 90%, время < 60 с | + чёткие метрики |

---

## 2. Клиентский путь — Вопрос-ответ (CP-FIZARUM-001)

| Аспект | v1 | prod-like (v2) | Diff |
|---|---|---|---|
| **Архитектура агента** | LangGraph (langgraph==0.5.3) | LangGraph + streaming | + streaming |
| **Модель агента** | DeepResearchAgent (OpenAI tool-loop + MemoryStore) | DeepResearchAgent + on_step callback | + incremental emission |
| **Polling** | GET /api/v1/tasks/{id} | То же, но с stateList | + stateList (RESEARCH/ANSWER) |
| **State list** | Отсутствует | stateList c type=RESEARCH/ANSWER | **Ключевое улучшение** |
| **Delta-история** | Вся история одним блоком | Delta-history через on_step callback | + инкрементальная выдача |
| **Pipeline** | blocking run() в asyncio.to_thread | То же | — |
| **System prompt** | Единый с бизнес-правилами | Единый с segment-of-one | — |

### Новое в v2:

- **Segment of one**: ответ зависит от запроса, контекста и данных
- **Streaming промежуточных состояний**: фронт видит RESEARCH по мере выполнения
- **StateList**: формализованные статусы RESEARCH/ANSWER

---

## 3. Клиентский путь — RCA (CP-FIZARUM-002)

| Аспект | v1 | prod-like (v2) | Diff |
|---|---|---|---|
| **Существование** | ❌ Отсутствует | ✅ Полноценный RCA pipeline | **+ НОВЫЙ КП** |
| **Pipeline** | — | BUILD → INVESTIGATE → SYNTHESIZE | — |
| **Агенты** | — | Front-agent, Tree Builder, Manager, Verifier, Synthesizer | — |
| **Дерево гипотез** | — | MECE-дерево (LLM, без данных) | — |
| **Визуализация** | — | D3.js интерактивное дерево | — |
| **Verdicts** | — | confirmed / rejected / inconclusive | — |
| **Память** | — | observation в S3 | — |

**Это ключевое расширение функциональности v2.**

---

## 4. Автономный процесс — Вопрос-ответ (AP-CP-FIZARUM-001)

| Аспект | v1 | prod-like (v2) | Diff |
|---|---|---|---|
| **Фреймворк** | LangGraph 0.5.3 | LangGraph + streaming | + streaming |
| **Модельная архитектура** | OpenAI tool-loop | То же | — |
| **LLM-модели** | Одна модель | Модель из реестра (model_registry) | + реестр моделей |
| **DeepSeek** | Поддерживается | Отсутствует | − DeepSeek |
| **GigaChat** | Опционален | Отсутствует | − GigaChat |
| **Parallel tool calls** | ThreadPoolExecutor | То же | — |
| **MemoryStore** | In-memory notes/findings | S3 + observation | + S3 persistence |
| **История** | PostgreSQL via SQLAlchemy | То же | — |
| **Business rules** | В system_prompt.txt | В prompts.py | + отдельные файлы промптов |
| **File parsers** | PDF/DOCX/XLSX/CSV/TXT/MD/PPTX | То же | — |
| **allowed_tables** | 15 таблиц в config | 15 таблиц в config | — |

### Новое в v2:

| Компонент | v1 | v2 |
|---|---|---|
| **Phoenix tracing** | Отсутствует | ✅ Phoenix/OpenTelemetry |
| **Таймауты** | Не формализованы | ✅ GRD-002 (60 с SQL) |
| **Лимит строк** | Не формализован | ✅ GRD-005 (10 000) |
| **model_registry** | Отсутствует (один LLM) | ✅ Реестр моделей, ModelSpec |

### Изменения в конфигурации:

| Параметр | v1 | v2 |
|---|---|---|
| **api_port** | 8080 | 8000 |
| **stand** | prod (default) | local (default) |
| **vector_mode** | opensearch (default) | lancedb (dev) / opensearch (prod) |
| **Embedding** | Qwen3-Embedding-8B | Та же |
| **Tracing** | Отсутствует | Phoenix |
| **KAP audit** | ✅ kap_logging_url | ✅ То же |

---

## 5. BBB (Business Building Blocks) — Diff по каждому

### BBB-001: Поиск схемы данных

| Аспект | v1 | v2 | Diff |
|---|---|---|---|
| **Vector store** | OpenSearch (prod) / LanceDB (dev) | OpenSearch (prod) / LanceDB (dev) | — |
| **Embedding** | Qwen3-Embedding-8B | Qwen3-Embedding-8B | — |
| **Hybrid search** | semantic + full-text | То же | — |
| **Файлы описаний** | schemas.xlsx, table_registry.xlsx | То же | — |
| **Abbreviations** | OpenSearch index abbreviations | То же | — |
| **KPI methods** | OpenSearch index KPIMETH | То же | — |

### BBB-002: Исполнение SQL

| Аспект | v1 | v2 | Diff |
|---|---|---|---|
| **Target DB** | Greenplum (prod) / SQLite (dev) | То же | — |
| **Auth** | Kerberos (keytab + principal) | Kerberos | — |
| **Guardrails** | Не формализованы | GRD-001, GRD-002, GRD-005 | + формальные guardrails |
| **Allowed tables** | 15 таблиц | 15 таблиц | — |

### BBB-003: Парсинг файлов — без изменений

### BBB-004: Построение дерева гипотез

| Аспект | v1 | v2 | Diff |
|---|---|---|---|
| **Существование** | ❌ Отсутствует | ✅ Tree Builder | **+ НОВЫЙ BBB** |
| **Метод** | — | MECE-дерево (LLM, без данных) | — |

### BBB-005: Верификация гипотезы

| Аспект | v1 | v2 | Diff |
|---|---|---|---|
| **Существование** | ❌ Отсутствует | ✅ Manager + Verifier | **+ НОВЫЙ BBB** |
| **Архитектура** | — | LangGraph sub-graph | — |

### BBB-006: Синтез ответа/заключения

| Аспект | v1 | v2 | Diff |
|---|---|---|---|
| **RESPONSE** | Единый synthesis | То же | — |
| **Executive Summary** | ❌ Отсутствует | ✅ RCA synthesis | + RCA |
| **Формат** | NL-текст | NL-текст | — |

### BBB-007: Сохранение в память

| Аспект | v1 | v2 | Diff |
|---|---|---|---|
| **Механизм** | In-memory notes/findings | S3 observation store | **+ S3 persistence** |
| **Scope** | Сессия (volatile) | Долговременная | + персистентность |
| **Наблюдения** | save_note / save_findings | observation | унификация |

### BBB-008: Управление историей — без изменений

---

## 6. Атомарные операции — Diff

| ID | v1 | v2 | Diff |
|---|---|---|---|
| AO-001 Векторизация | ✅ | ✅ | — |
| AO-002 k-NN поиск | ✅ | ✅ | — |
| AO-003 Подключение к БД | ✅ | ✅ | — |
| AO-004 Исполнение SQL | ✅ | ✅ | — |
| AO-005 Парсинг файлов | ✅ | ✅ | — |
| AO-006 LLM Tree Builder | ❌ | ✅ | **+ НОВАЯ** |
| AO-007 LLM Manager/Verifier | ❌ | ✅ | **+ НОВАЯ** |
| AO-008 LLM Synthesizer | ✅ | ✅ | — |
| AO-009 S3 save | ❌ | ✅ | **+ НОВАЯ** |
| AO-010 CRUD история | ✅ | ✅ | — |

---

## 7. Guardrails — Diff

| ID | v1 | v2 | Diff |
|---|---|---|---|
| GRD-001 Только SELECT | Неявный (в коде) | ✅ Формализован | + явный guardrail |
| GRD-002 Таймаут SQL | Неявный (asyncio) | ✅ 60 с | + явный таймаут |
| GRD-003 MAX_VERIFY_ROUNDS | ❌ | ✅ RCA | **+ НОВЫЙ** |
| GRD-004 Глобальный таймаут | Неявный | ✅ 10 мин | + явный |
| GRD-005 Лимит строк | db_max_rows=500 | ✅ 10 000 | + формализация |

---

## 8. Роли — Diff

| Роль | v1 | v2 | Diff |
|---|---|---|---|
| Владелец продукта | Не определен | Определён | + |
| Технический владелец | Не определен | Определён | + |
| Gate / Контролёр | Не определен | Определён | + |

---

## Сводка прогресса

### Объекты управления

| Тип | v1 | v2 | Diff |
|---|---|---|---|
| Потребность | 1 | 1 | — |
| Клиентские пути | 1 | **2** | **+ 1 (RCA)** |
| Автономные процессы | 1 | **2** | **+ 1 (RCA)** |
| BBB | 4 | **8** | **+ 4 (гипотезы, верификация, S3, —)** |
| Атомарные операции | 6 | **10** | **+ 4 (Tree Builder, Verifier, S3, —)** |
| Guardrails | 0 | **5** | **+ 5 (все формализованы)** |
| Роли | 0 | **3** | **+ 3** |

### Ключевые улучшения

1. **RCA-режим** — полностью новая функциональность
2. **Streaming** — инкрементальная выдача RESEARCH-шагов
3. **StateList** — формализованные состояния исполнения
4. **S3 Memory** — долговременная память вместо in-memory
5. **Formal guardrails** — от неявных к явным ограничениям
6. **Phoenix tracing** — наблюдаемость исполнения
7. **Модельный реестр** — выбор LLM через model_registry
8. **Визуализация RCA** — D3.js дерево гипотез

### Что осталось без изменений

- Базовая архитектура (FastAPI + LangGraph)
- Business domain (HR, оргструктура, KPI)
- Data sources (Greenplum, OpenSearch, LanceDB)
- Модель данных (15 таблиц)
- API-контракт (REST + polling)

---

*Вывод: проект вырос из single-purpose NL→SQL агента в multi-mode аналитическую платформу с RCA, streaming, формальными guardrails и персистентной памятью. Ключевой драйвер — RCA-режим.*