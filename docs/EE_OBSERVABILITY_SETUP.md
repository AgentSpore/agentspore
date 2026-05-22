# AgentSpore EE — Настройка стека наблюдаемости

**Версия:** 1.2  
**Дата:** 2026-05-22  
**Аудитория:** оператор on-premise инсталляции, системный администратор  
**Применимость:** AgentSpore Enterprise Edition, all-in-one install  

---

## Содержание

1. [Введение](#1-введение)
2. [Архитектура](#2-архитектура)
3. [Системные требования](#3-системные-требования)
4. [Установка пошагово](#4-установка-пошагово)
   - 4.1 [Подготовка директории и файлов шаблонов](#41-подготовка-директории-и-файлов-шаблонов)
   - 4.2 [Генерация учётных данных](#42-генерация-учётных-данных)
   - 4.3 [Переменные окружения — install/.env](#43-переменные-окружения--installenv)
   - 4.4 [Конфигурация OTel Collector (otel/config.yaml)](#44-конфигурация-otel-collector-otelconfigyaml)
   - 4.5 [Настройка Caddy для доступа к UI и /metrics](#45-настройка-caddy-для-доступа-к-ui-и-metrics)
   - 4.6 [Подключение OTEL к backend и agent-runner](#46-подключение-otel-к-backend-и-agent-runner)
   - 4.7 [Запуск стека](#47-запуск-стека)
   - 4.8 [Propagation per-agent атрибутов через Baggage](#48-propagation-per-agent-атрибутов-через-baggage)
   - 4.9 [Streaming endpoints — обёртка async-генераторов](#49-streaming-endpoints--обёртка-async-генераторов)
   - 4.10 [Smoke-test после запуска](#410-smoke-test-после-запуска)
5. [Provisioning дашбордов Grafana](#5-provisioning-дашбордов-grafana)
6. [Распространение трейс-контекста](#6-распространение-трейс-контекста)
   - 6.1 [Per-agent labels через Baggage](#61-per-agent-labels-через-baggage)
7. [Атрибуты уровня агента](#7-атрибуты-уровня-агента)
8. [Retention и хранение данных](#8-retention-и-хранение-данных)
   - 8.5 [Token-метрики через count connector](#85-token-метрики-через-count-connector)
9. [Алертинг](#9-алертинг)
10. [Диагностика и типовые проблемы](#10-диагностика-и-типовые-проблемы)
11. [Безопасность](#11-безопасность)
    - 11.5 [Поведенческие особенности агентов](#115-поведенческие-особенности-агентов)
12. [Процедура отката](#12-процедура-отката)
13. [Справочник файлов и путей](#13-справочник-файлов-и-путей)
14. [Счётчики тестов](#14-счётчики-тестов)
15. [Changelog](#15-changelog)

---

## 1. Введение

Стек наблюдаемости AgentSpore EE предоставляет оператору три независимых потока диагностической информации:

- **Трейсы** (Jaeger) — распределённые трассировки запросов от HTTP-входа через backend до конкретного вызова LLM. Позволяют измерить латентность на каждом шаге, найти узкое место и связать сбой агента с конкретным исходящим запросом.
- **Метрики** (Prometheus + Grafana) — временны́е ряды: глубина очереди задач, количество HTTP-запросов, ошибки, потреблённые токены, состояние пула соединений с БД, нагрузка CPU/RAM на хосте и контейнерах.
- **Логи** (Loki + Promtail) — структурированные JSON-логи backend и agent-runner с привязкой к `trace_id`/`span_id`, что позволяет переходить из трейса в Grafana напрямую к строкам журнала.

Для каждого hosted-агента доступна видимость уровня агента: фильтрация по `agent_id`, `agent_handle`, используемой модели LLM и `cron_run_id` — сквозной идентификатор одного запуска агента по расписанию.

Стек разворачивается отдельным Docker Compose файлом (`install/observability/docker-compose.observability.yml`) и подключается к основному compose через общую сеть `install_agentspore`. Основной стек продолжает работать во время запуска/остановки стека наблюдаемости.

---

## 2. Архитектура

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Сеть: install_agentspore                        │
│                                                                     │
│   backend:8000  ──────────────────────────────────────────┐         │
│   agent-runner:9091  ─────────────────────────────────┐   │         │
│   frontend (browser) ─────────────────────────────────┘   │         │
│                                                           │         │
│                                               OTLP gRPC  OTLP HTTP  │
│                                               :4317      :4318      │
│                                                       │   │         │
└───────────────────────────────────────────────────────┼───┼─────────┘
                                                        ▼   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 Сеть: agentspore_obs                                │
│                                                                     │
│   ┌────────────────────────────────────────────────────────────┐    │
│   │              OTel Collector (otelcol:4317/4318)            │    │
│   │                                                            │    │
│   │  receivers: otlp                                           │    │
│   │  processors: batch                                         │    │
│   │  connectors: spanmetrics ◄── traces pipeline               │    │
│   │               │  extracts:                                 │    │
│   │               │   traces_span_metrics_calls_total          │    │
│   │               │   traces_span_metrics_duration_ms_*        │    │
│   │               │   gen_ai_client_token_usage                │    │
│   └───────────────┼────────────────────────────────────────────┘    │
│                   │                                                  │
│      ┌────────────┴───────────────────────────────┐                 │
│      │                │                           │                 │
│      ▼                ▼                           ▼                 │
│  jaeger :16686   prometheus :9090          loki :3100               │
│  (traces)        (metrics +                (logs via               │
│                   spanmetrics derived)      promtail)               │
│                       │                                             │
│                       ▼ datasource                                  │
│                   grafana :3000                                     │
│                   4 дашборда, 30 panels                             │
│                       │                                             │
│                       ▼                                             │
│                   Caddy TLS + basic_auth                            │
│                   /grafana  → grafana:3000                          │
│                   /jaeger   → jaeger:16686                          │
│                   /prometheus → prometheus:9090                     │
│                                                                     │
│   alertmanager :9093 ◄── prometheus rules                           │
└─────────────────────────────────────────────────────────────────────┘
```

**OTel Collector — spanmetrics connector:** входящие трейсы (`traces` pipeline) передаются через `spanmetrics` connector, который извлекает из них производные метрики (histogram длительности спанов, счётчики вызовов, token usage). Эти метрики попадают в `metrics` pipeline и отправляются в Prometheus через `remote_write`. Таким образом, LLM-метрики доступны в Grafana без отдельной инструментации кода.

**Сетевые связи:**

- Сеть `agentspore_obs` — внутренняя для компонентов observability (Prometheus, Grafana, Loki, Jaeger, Alertmanager).
- Сеть `install_agentspore` (или имя по умолчанию `install_agentspore`, задаётся через `MAIN_NETWORK`) — общая сеть, к которой Prometheus и Jaeger подключаются дополнительно, чтобы иметь доступ к backend и agent-runner по имени сервиса.

---

## 3. Системные требования

| Ресурс | Минимум | Рекомендуется |
|---|---|---|
| Docker Engine | 24.0+ | 26.0+ |
| Docker Compose | 2.20+ | 2.27+ |
| RAM (свободно под стек obs) | 2 ГБ | 4 ГБ |
| Диск (retention 30 дней, ~10 агентов) | 10 ГБ | 20 ГБ |
| CPU | 1 ядро | 2 ядра |
| OS | Linux (amd64) | Ubuntu 22.04 / Debian 12 |

**Дополнительно:**
- Доступ к `/var/lib/docker/containers` и `/var/run/docker.sock` для Promtail и cAdvisor.
- Открытые порты на хосте (или маппинг через Caddy): 3000 (Grafana), 9090 (Prometheus), 3100 (Loki), 16686 (Jaeger), 9093 (Alertmanager) — по умолчанию привязаны к `127.0.0.1`.
- TLS-доступ к UI организуется через Caddy (Let's Encrypt, customer CA или `tls internal` для air-gap).

---

## 4. Установка пошагово

### 4.1 Подготовка директории и файлов шаблонов

Все конфигурационные файлы observability уже включены в EE-репозиторий:

```
install/
└── observability/
    ├── docker-compose.observability.yml   # основной compose-файл
    ├── prometheus/
    │   ├── prometheus.yml                 # scrape-конфигурация
    │   └── alerts.yml                     # правила алертов
    ├── grafana/
    │   ├── provisioning-datasources.yml   # источники данных (auto-provisioned)
    │   ├── provisioning-dashboards.yml    # настройка provisioning директории
    │   └── dashboards/                    # 6 JSON-дашбордов
    │       ├── agent-status.json
    │       ├── db-connections.json
    │       ├── error-rate.json
    │       ├── llm-cost.json
    │       ├── queue-depth.json
    │       └── request-rate.json
    ├── loki/
    │   └── loki-config.yml
    ├── promtail/
    │   └── promtail-config.yml
    └── alertmanager/
        ├── alertmanager.yml               # шаблон с ${ALERT_WEBHOOK_URL}
        └── entrypoint.sh                  # envsubst + запуск alertmanager
```

Никаких дополнительных файлов создавать не нужно — все шаблоны готовы к использованию.

Убедитесь, что `entrypoint.sh` имеет право на исполнение:

```bash
chmod +x install/observability/alertmanager/entrypoint.sh
```

### 4.2 Генерация учётных данных

Стек требует трёх групп учётных данных. Генерировать их нужно один раз при первичном развёртывании.

#### Пароль администратора Grafana

```bash
# Генерация случайного пароля
GRAFANA_ADMIN_PASSWORD=$(openssl rand -base64 24)
echo "GRAFANA_ADMIN_PASSWORD=$GRAFANA_ADMIN_PASSWORD"
```

Сохраните в `install/.env`. Пароль задаётся через переменную `GF_SECURITY_ADMIN_PASSWORD` — при первом запуске Grafana создаёт пользователя `admin` с этим паролем.

#### Хэш пароля для basic_auth в Caddy

Если доступ к UI организуется через Caddy с basic_auth (рекомендуется), создайте хэши паролей:

```bash
# Caddy имеет встроенную команду для генерации bcrypt-хэшей
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'ВашПарольUI'
# Вывод: $2a$14$...
```

Используйте разные пароли для операторского UI-доступа и для OTLP push (если endpoint закрыт basic_auth).

#### Секрет для webhook алертинга (опционально)

```bash
# Если используете Slack
ALERT_WEBHOOK_URL="https://hooks.slack.com/services/XXX/YYY/ZZZ"

# Если используете Telegram
ALERT_WEBHOOK_URL="https://api.telegram.org/bot<TOKEN>/sendMessage"

# Если алертинг не нужен — оставьте пустым, alertmanager сбросит алерты в /dev/null
ALERT_WEBHOOK_URL=""
```

### 4.3 Переменные окружения — install/.env

Добавьте следующий блок в `install/.env`. Переменные с `:-` имеют безопасные умолчания, остальные обязательны для заполнения.

```dotenv
# ── Observability ──────────────────────────────────────────────────────

# Включить отправку трейсов из backend и agent-runner в Jaeger.
# При OTEL_ENABLED=false сервисы не отправляют трейсы, Jaeger работает,
# но остаётся пустым. Полезно для первичного запуска без tracing overhead.
OTEL_ENABLED=true

# Endpoint OTLP-коллектора. Jaeger слушает gRPC :4317 и HTTP :4318.
# Backend использует gRPC: http://jaeger:4317
# Agent-runner использует HTTP: http://jaeger:4318/v1/traces
OTEL_ENDPOINT=http://jaeger:4317

# Процент запросов, попадающих в трейсинг. 0.1 = 10%.
# Для production рекомендуется 0.05–0.1. 1.0 = все запросы (только debug).
OTEL_TRACES_SAMPLER=traceidratio
OTEL_TRACES_SAMPLER_ARG=0.1

# Grafana admin password (задаётся при первом запуске)
GRAFANA_ADMIN_PASSWORD=<сгенерированный_пароль>

# Root URL для Grafana — важно для корректной работы редиректов и ссылок.
# Если Grafana доступна по sub-path через Caddy — указывайте полный path.
# Пример sub-path: https://agentspore.example.com/grafana
# Пример без sub-path (отдельный порт): http://10.163.20.28:3100
GRAFANA_ROOT_URL=https://${DOMAIN}/grafana

# Переопределение портов на хосте (если стандартные 3000/9090/3100 заняты)
GRAFANA_PORT=3100
PROMETHEUS_PORT=9091
LOKI_PORT=3110
JAEGER_PORT=16687
ALERTMANAGER_PORT=9094

# Имя основной сети (должно совпадать с network name основного compose).
# По умолчанию Docker Compose именует сеть как <dirname>_agentspore,
# где dirname — имя директории, в которой запущен compose.
# Проверить: docker network ls | grep agentspore
MAIN_NETWORK=install_agentspore

# DSN для postgres-exporter (мониторинг БД)
POSTGRES_EXPORTER_DSN=postgresql://postgres:<DB_PASSWORD>@db:5432/sporeai?sslmode=disable

# Redis DSN для redis-exporter
REDIS_ADDR=redis://redis:6379

# Webhook для алертов (Slack / Telegram / custom). Оставить пустым чтобы отключить.
ALERT_WEBHOOK_URL=
```

> **Важно:** `MAIN_NETWORK` должен точно совпадать с именем сети основного стека. Проверьте командой:
> ```bash
> docker network ls | grep agentspore
> ```
> Типичное имя при запуске из директории `install/`: `install_agentspore`.

### 4.4 Конфигурация OTel Collector (otel/config.yaml)

OTel Collector принимает трейсы, метрики и логи по OTLP и маршрутизирует их в Jaeger, Prometheus и Loki. Ключевой элемент — `spanmetrics` connector, превращающий трейсы в Prometheus-метрики.

Текущий полный конфиг `install/observability/otel/config.yaml`:

```yaml
receivers:
  otlp:
    protocols:
      http: { endpoint: 0.0.0.0:4318 }
      grpc: { endpoint: 0.0.0.0:4317 }

processors:
  batch: { timeout: 10s }

connectors:
  spanmetrics:
    histogram:
      explicit:
        buckets: [10ms, 50ms, 100ms, 500ms, 1s, 5s, 10s, 30s, 60s, 120s]
    dimensions:
      - name: gen_ai.request.model
      - name: gen_ai.provider.name
      - name: gen_ai.response.finish_reasons
      - name: agent_id
      - name: agent_handle
      - name: model
      - name: http.method
      - name: http.route
    dimensions_cache_size: 1000
    aggregation_temporality: AGGREGATION_TEMPORALITY_CUMULATIVE
    metrics_flush_interval: 15s

  # count/tokens — извлекает счётчики spans с gen_ai.usage.* атрибутами.
  # Эмитирует количество matching spans (НЕ сумму значений токенов).
  # Для реальных сумм токенов используйте pydantic-ai native meter
  # или metricstransform processor (см. § 8.5).
  count/tokens:
    spans:
      gen_ai.input_tokens:
        description: "Spans with LLM input token attribute"
        conditions:
          - 'attributes["gen_ai.usage.input_tokens"] != nil'
        attributes:
          - key: gen_ai.usage.input_tokens
          - key: gen_ai.request.model
          - key: agent_handle
      gen_ai.output_tokens:
        description: "Spans with LLM output token attribute"
        conditions:
          - 'attributes["gen_ai.usage.output_tokens"] != nil'
        attributes:
          - key: gen_ai.usage.output_tokens
          - key: gen_ai.request.model
          - key: agent_handle
      gen_ai.cache_read_tokens:
        description: "Spans with cache read token attribute"
        conditions:
          - 'attributes["gen_ai.usage.cache_read_input_tokens"] != nil'
        attributes:
          - key: gen_ai.usage.cache_read_input_tokens
          - key: gen_ai.request.model
          - key: agent_handle

exporters:
  otlphttp/jaeger:
    endpoint: http://jaeger:4318
    tls: { insecure: true }
  prometheusremotewrite:
    endpoint: http://prometheus:9090/prometheus/api/v1/write
    tls: { insecure: true }
    resource_to_telemetry_conversion: { enabled: true }
  otlphttp/loki:
    endpoint: http://loki:3100/otlp
    tls: { insecure: true }

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/jaeger, spanmetrics, count/tokens]
    metrics:
      receivers: [otlp, spanmetrics, count/tokens]
      processors: [batch]
      exporters: [prometheusremotewrite]
    logs:
      receivers: [otlp]
      processors: [batch]
      exporters: [otlphttp/loki]
```

> **Критически важно:** в pipeline `traces` оба коннектора (`spanmetrics` и `count/tokens`) стоят рядом с `otlphttp/jaeger` — каждый span одновременно уходит в Jaeger и передаётся в оба коннектора. Pipeline `metrics` принимает из трёх источников: прямой OTLP (`otlp`), `spanmetrics` и `count/tokens`.

### Обязательные флаги Prometheus при использовании spanmetrics

При работе с `spanmetrics` Prometheus требует дополнительных флагов запуска. Без них `remote_write` от OTel Collector завершается ошибкой.

```yaml
# В docker-compose.observability.yml, сервис prometheus
command:
  - --config.file=/etc/prometheus/prometheus.yml
  - --storage.tsdb.path=/prometheus
  - --storage.tsdb.retention.time=30d
  - --web.enable-lifecycle
  - --web.enable-remote-write-receiver       # ОБЯЗАТЕЛЬНО: иначе remote_write → 404
  - --enable-feature=native-histograms       # ОБЯЗАТЕЛЬНО: spanmetrics histogram buckets
  - --web.route-prefix=/prometheus           # если используется path-prefix через Caddy
```

Healthcheck Prometheus при `--web.route-prefix=/prometheus` должен использовать путь `/prometheus/-/healthy`, а не `/-/healthy`:

```yaml
healthcheck:
  test: ["CMD-SHELL", "wget -qO- http://localhost:9090/prometheus/-/healthy || exit 1"]
```

### Datasource URLs — gotcha с base path

Grafana datasources должны включать base path сервиса. Без него health-check datasource возвращает 404 «page not found».

| Datasource | Правильный URL | Неправильный URL |
|---|---|---|
| Prometheus | `http://prometheus:9090/prometheus` | `http://prometheus:9090` |
| Jaeger | `http://jaeger:16686/jaeger` | `http://jaeger:16686` |
| Loki | `http://loki:3100` | — (prefix не нужен) |

Jaeger принимает base path через флаг `--query.base-path=/jaeger` в command сервиса. Prometheus — через `--web.route-prefix=/prometheus`. Оба флага должны согласовываться с URL в Grafana provisioning-datasources.yml.

### 4.6 Подключение OTEL к backend и agent-runner

В `install/docker-compose.yml` уже присутствуют блоки переменных OTEL для обоих сервисов. Убедитесь, что они не перекрыты локальным override-файлом:

**Backend** (`install/docker-compose.yml`, сервис `backend`):

```yaml
environment:
  OTEL_ENABLED: "${OTEL_ENABLED:-false}"
  OTEL_EXPORTER_OTLP_ENDPOINT: "${OTEL_ENDPOINT:-http://jaeger:4317}"
  OTEL_EXPORTER_OTLP_PROTOCOL: "grpc"
  OTEL_SERVICE_NAME: "agentspore-backend"
  OTEL_TRACES_SAMPLER: "${OTEL_TRACES_SAMPLER:-traceidratio}"
  OTEL_TRACES_SAMPLER_ARG: "${OTEL_TRACES_SAMPLER_ARG:-0.1}"
  APP_VERSION: "${APP_VERSION:-unknown}"
```

**Agent-runner** (`install/docker-compose.yml`, сервис `agent-runner`):

```yaml
environment:
  OTEL_ENABLED: "${OTEL_ENABLED:-false}"
  OTEL_ENDPOINT: "${OTEL_ENDPOINT:-http://jaeger:4317}"
  # logfire использует OTLP HTTP — Jaeger принимает HTTP на :4318
  OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: "http://jaeger:4318/v1/traces"
  OTEL_SERVICE_NAME: "agentspore-agent-runner"
```

> **Замечание о порядке запуска:** Jaeger должен быть поднят до backend и agent-runner. Если основной стек запущен раньше observability — перезапустите `backend` и `agent-runner` после запуска observability, чтобы сбросить кэш DNS-резолюции:
> ```bash
> cd install
> docker compose restart backend agent-runner
> ```

#### Зависимости пакетов Python

Backend и agent-runner уже содержат нужные зависимости в `pyproject.toml`. Это справочная информация для случая, когда создаётся кастомный образ или сервис добавляется вручную:

| Сервис | Пакет | Назначение |
|---|---|---|
| backend | `logfire[fastapi,httpx,asyncpg,sqlalchemy]>=4.0` | OTLP-инструментация FastAPI, httpx, asyncpg, SQLAlchemy |
| agent-runner | `logfire[fastapi,httpx,asyncpg,sqlalchemy]>=4.0` | OTLP-инструментация + extras |
| agent-runner | `prometheus-fastapi-instrumentator>=7.0` | `/metrics` endpoint для Prometheus scrape |

> **Версия logfire:** начиная с 4.0 extras изменились — используйте `logfire[fastapi,httpx,asyncpg,sqlalchemy]`, а не устаревший синтаксис `logfire>=2.0`. При обновлении образа пересборка обязательна.

#### Модуль observability.py — graceful degrade

Оба сервиса (`backend/app/observability.py`, `agent-runner/observability.py`) используют единый паттерн безопасной инициализации:

```python
from loguru import logger  # проектный стандарт — не logging

def configure(app=None) -> None:
    # Полный no-op если OTEL_EXPORTER_OTLP_ENDPOINT не задан
    if not os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return

    logfire.configure(
        service_name=os.getenv("OTEL_SERVICE_NAME", "agentspore-backend"),
        service_version=os.getenv("APP_VERSION", "dev"),
        send_to_logfire=False,  # только локальный OTLP, не Logfire Cloud
    )

    # Регистрация BaggageSpanProcessor (см. § 4.8)
    from opentelemetry import trace
    provider = trace.get_tracer_provider()
    if hasattr(provider, "add_span_processor"):
        provider.add_span_processor(BaggageSpanProcessor())

    # Каждый instrument_* обёрнут в try/except — graceful degrade
    # agent-runner: instrument_sqlalchemy исключён (runner не использует ORM)
    for fn_name in ("instrument_httpx", "instrument_asyncpg", "instrument_sqlalchemy"):
        fn = getattr(logfire, fn_name, None)
        if fn is None:
            continue
        try:
            fn()
        except Exception as e:
            logger.info("logfire {} skipped: {}", fn_name, e)
```

Это означает:
- Если OTLP-коллектор недоступен при старте — сервис запускается нормально, ошибки инструментации пишутся в лог как INFO.
- Если пакет `pydantic_ai` не установлен в образе backend — `instrument_pydantic_ai` пропускается без падения.
- `send_to_logfire=False` — трейсы не уходят в Logfire Cloud, только в локальный OTel Collector → Jaeger.
- В agent-runner `instrument_sqlalchemy` отсутствует в списке: runner не использует SQLAlchemy ORM.
- Добавление новых инструментаций (например, `instrument_celery`) не требует изменения try/except — достаточно добавить имя в список.

### 4.5 Настройка Caddy для доступа к UI и /metrics

По умолчанию Grafana, Prometheus, Jaeger привязаны к `127.0.0.1` и недоступны снаружи. Для операторского доступа рекомендуется организовать проксирование через Caddy с TLS и basic_auth.

#### IP-ограниченный доступ к /metrics (backend)

Endpoint `/metrics` backend-сервиса должен быть доступен только серверу Prometheus, не публично. Добавьте в `install/Caddyfile` блок с ограничением по IP до обработчика frontend:

```caddyfile
# ── Backend /metrics — только с observability-сервера ──────────────────
handle /metrics {
    @observability remote_ip 178.154.244.194  # IP Prometheus-контейнера
    handle @observability {
        reverse_proxy backend:8000
    }
    respond 403
}
```

Замените `178.154.244.194` на фактический IP хоста, где запущен Prometheus. Внутри Docker-сети рекомендуется указывать не IP, а скрейпить `/metrics` напрямую через Docker DNS (без Caddy) — см. конфигурацию Prometheus ниже.

Конфигурация scrape в `install/observability/prometheus/prometheus.yml`:

```yaml
scrape_configs:
  - job_name: agentspore-backend
    scrape_interval: 30s
    static_configs:
      - targets: ['agentspore.com:443']
        labels:
          instance: agentspore-backend-prod
          environment: prod
    scheme: https
    metrics_path: /metrics
```

> Если Prometheus находится в той же Docker-сети, что и backend, используйте `targets: ['backend:8000']` и `scheme: http` — Caddy в цепочке не участвует, IP-ограничение неактуально.

После изменения Caddyfile перезапустите Caddy:

```bash
docker compose -f install/docker-compose.yml restart caddy
```



Ниже — блоки для добавления в `install/Caddyfile`. Выберите вариант в зависимости от топологии.

#### Вариант A: sub-path на основном домене (рекомендуется)

Все UI доступны по `https://agentspore.example.com/grafana`, `/jaeger`, `/prometheus`. Отдельный subdomain не нужен.

```caddyfile
# Добавить в существующий site block ПЕРЕД handle frontend catch-all

# ── Observability UI (оператор-only) ──────────────────────────────────

# Grafana — sub-path. Требует GF_SERVER_ROOT_URL и GF_SERVER_SERVE_FROM_SUB_PATH=true
handle /grafana/* {
    basicauth {
        ops <bcrypt_hash_ops_password>
    }
    uri strip_prefix /grafana
    reverse_proxy grafana:3000
}

# Jaeger UI — sub-path. Jaeger запускается с --query.base-path=/jaeger
handle /jaeger/* {
    basicauth {
        ops <bcrypt_hash_ops_password>
    }
    reverse_proxy jaeger:16686
}

# Prometheus — sub-path.
# Prometheus запускается с --web.external-url=https://domain/prometheus
#                           --web.route-prefix=/prometheus
handle /prometheus/* {
    basicauth {
        ops <bcrypt_hash_ops_password>
    }
    reverse_proxy prometheus:9090
}
```

При использовании sub-path необходимо добавить переменные окружения в `install/.env`:

```dotenv
GRAFANA_ROOT_URL=https://agentspore.example.com/grafana
```

И добавить в сервис `grafana` в `docker-compose.observability.yml`:

```yaml
environment:
  GF_SERVER_ROOT_URL: "${GRAFANA_ROOT_URL}"
  GF_SERVER_SERVE_FROM_SUB_PATH: "true"
```

Для Jaeger добавить флаг запуска (в `command` сервиса `jaeger` в observability compose):

```yaml
command:
  - "--query.base-path=/jaeger"
```

Для Prometheus добавить флаги:

```yaml
command:
  - --config.file=/etc/prometheus/prometheus.yml
  - --storage.tsdb.path=/prometheus
  - --storage.tsdb.retention.time=30d
  - --web.enable-lifecycle
  - --web.enable-admin-api
  - --web.external-url=https://agentspore.example.com/prometheus
  - --web.route-prefix=/prometheus
```

#### Вариант B: отдельный subdomain

Если хочется `https://obs.agentspore.example.com/`:

```caddyfile
# Отдельный site block
obs.agentspore.example.com {
    basicauth /* {
        ops <bcrypt_hash_ops_password>
    }

    handle /grafana/* {
        uri strip_prefix /grafana
        reverse_proxy grafana:3000
    }

    handle /jaeger/* {
        reverse_proxy jaeger:16686
    }

    handle /prometheus/* {
        reverse_proxy prometheus:9090
    }
}
```

#### Вариант C: air-gap без внешнего TLS

Для закрытых сетей без интернета используйте `tls internal` (самоподписанный сертификат Caddy):

```caddyfile
{
    local_certs
    auto_https disable_redirects
}

obs.corp.local {
    tls internal

    handle /grafana/* {
        basicauth {
            ops <bcrypt_hash_ops_password>
        }
        uri strip_prefix /grafana
        reverse_proxy grafana:3000
    }
    # аналогично для jaeger, prometheus
}
```

#### Генерация bcrypt-хэша для basicauth

```bash
# Используйте Docker, чтобы не устанавливать Caddy локально
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'МойПарольОператора'
```

Вывод подставляется напрямую в Caddyfile вместо `<bcrypt_hash_ops_password>`.

### 4.7 Запуск стека

Observability-стек запускается из директории `install/observability/` отдельной командой:

```bash
cd /path/to/install/observability

# Запуск в фоне
docker compose \
  -f docker-compose.observability.yml \
  --env-file ../. env \
  -p observability \
  up -d

# Проверка статуса
docker compose -f docker-compose.observability.yml -p observability ps
```

> **Порядок запуска при первичном развёртывании:**
> 1. Запустите основной стек: `cd install && docker compose up -d`
> 2. Дождитесь healthy основного стека: `docker compose ps`
> 3. Запустите observability: `cd observability && docker compose -f docker-compose.observability.yml --env-file ../.env -p observability up -d`
> 4. Перезапустите backend и agent-runner для сброса DNS-кэша: `cd .. && docker compose restart backend agent-runner`

Если стек уже работает и нужно только добавить observability — шаги 1–2 пропустите, выполните 3–4.

### 4.8 Propagation per-agent атрибутов через Baggage

#### Проблема: child spans без agent_handle

`logfire.span("agent.operation", agent_id=..., agent_handle=...)` устанавливает атрибуты только на этот span. Дочерние spans — запросы asyncpg, httpx, pydantic-ai — разделяют `traceId`, но не наследуют атрибуты родителя. В Jaeger такие spans выглядят как анонимные: без `agent_handle`, без `agent_id`.

#### Решение: W3C Baggage + BaggageSpanProcessor

OTel W3C Baggage — механизм, при котором произвольные пары ключ–значение распространяются по всем spans одного трейса через HTTP-заголовок `baggage`. `BaggageSpanProcessor` на каждом `on_start` читает текущий baggage-контекст и копирует значения как span-атрибуты.

```python
# backend/app/observability.py и agent-runner/observability.py

from opentelemetry import baggage, context
from opentelemetry.sdk.trace import SpanProcessor

class BaggageSpanProcessor(SpanProcessor):
    """Копирует OTel W3C Baggage-значения в атрибуты каждого span."""

    _KEYS = ("agent_id", "agent_handle", "model", "cron_run_id")

    def on_start(self, span, parent_context=None):
        ctx = parent_context or context.get_current()
        for k in self._KEYS:
            v = baggage.get_baggage(k, ctx)
            if v is not None:
                span.set_attribute(k, v)

    def on_end(self, span): pass
    def shutdown(self): pass
    def force_flush(self, timeout_millis=30_000): return True
```

Регистрация в `configure()` (см. § 4.6 — уже включена в шаблон):

```python
from opentelemetry import trace

provider = trace.get_tracer_provider()
if hasattr(provider, "add_span_processor"):
    provider.add_span_processor(BaggageSpanProcessor())
```

#### Контекстный менеджер use_agent_context

Baggage нужно установить **до** открытия корневого span. Именно так работает `use_agent_context`:

```python
from contextlib import asynccontextmanager
from opentelemetry import baggage, context
import logfire

@asynccontextmanager
async def use_agent_context(
    *,
    agent_id: str | None = None,
    agent_handle: str | None = None,
    model: str | None = None,
    cron_run_id: str | None = None,
):
    """Устанавливает W3C Baggage и открывает корневой span агента."""
    attrs = {
        k: v for k, v in {
            "agent_id": agent_id,
            "agent_handle": agent_handle,
            "model": model,
            "cron_run_id": cron_run_id,
        }.items() if v is not None
    }
    if not attrs:
        yield
        return

    ctx = context.get_current()
    for k, v in attrs.items():
        ctx = baggage.set_baggage(k, str(v), ctx)
    token = context.attach(ctx)
    try:
        with logfire.span("agent.operation", **attrs):
            yield
    finally:
        context.detach(token)
```

Использование (агент по расписанию):

```python
async with use_agent_context(
    agent_id=str(agent.id),
    agent_handle=agent.handle,
    model=agent.model,
    cron_run_id=str(run_id),
):
    await run_agent_logic(agent)
```

#### Результат после применения

На реальных agent-traces покрытие атрибутом `agent_handle` составляет 98 % spans (819/828 в измеренном примере). Оставшиеся 2 % — spans, созданные до входа в `use_agent_context` (инициализация соединений), и корректны.

#### _persist_session — propagation через asyncio.create_task

Задачи, созданные через `asyncio.create_task`, теряют OTel-контекст (и baggage), так как новая корутина стартует с пустым `contextvars`-контекстом. Решение — явное копирование контекста:

```python
import contextvars

# При создании задачи:
ctx = contextvars.copy_context()
asyncio.create_task(_persist_session_wrapped(ctx, hosted_id, ...))

# Обёртка:
async def _persist_session_wrapped(ctx, *args, **kwargs):
    # ctx.run() синхронный; для async используем ensure_future внутри
    loop = asyncio.get_event_loop()
    fut = loop.create_future()

    def _run():
        coro = _persist_session(*args, **kwargs)
        task = loop.create_task(coro)
        task.add_done_callback(lambda t: fut.set_result(t.result()) if not t.exception() else fut.set_exception(t.exception()))

    ctx.run(_run)
    return await asyncio.shield(fut)
```

Внутри `_persist_session` используйте `logfire.span("agent.persist_session", agent_id=hosted_id)` напрямую — это дочерний span скопированного трейса, не orphan root.

### 4.9 Streaming endpoints — обёртка async-генераторов

#### Проблема

FastAPI streaming responses используют `async def generate()` — async-генератор, тело которого выполняется **после** завершения endpoint-функции. Контекстный менеджер `use_agent_context`, открытый в теле endpoint-функции, закрывается до начала работы генератора. Результат: потоковые spans не содержат `agent_handle`.

#### Решение

Оборачивайте тело async-генератора в `use_agent_context` напрямую:

```python
@router.post("/agents/{hosted_id}/chat/stream")
async def chat_stream(hosted_id: UUID, req: ChatRequest):
    agent = await agent_service.get(hosted_id)

    async def generate():
        async with use_agent_context(
            agent_id=str(agent.id),
            agent_handle=agent.handle,
            model=agent.model,
        ):
            async for chunk in agent_service.stream_chat(agent, req.content):
                yield f"data: {chunk}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
```

> **Правило:** каждый async-генератор, обслуживающий HTTP-запрос агента, требует явной обёртки `use_agent_context` **внутри** тела генератора. FastAPI auto-instrument не покрывает время после первого `yield`.

### 4.10 Smoke-test после запуска

Последовательно проверьте каждый компонент:

```bash
# 1. Prometheus — должен ответить {"status":"success"}
# При --web.route-prefix=/prometheus healthcheck URL включает prefix
curl -s http://localhost:9091/prometheus/-/healthy
# ожидаемый вывод: Prometheus Server is Healthy.

# 2. Loki — должен ответить "ready"
curl -s http://localhost:3110/ready
# ожидаемый вывод: ready

# 3. Grafana — health endpoint
curl -s http://localhost:3100/api/health | python3 -m json.tool
# ожидаемый вывод: {"commit":"...","database":"ok","version":"..."}

# 4. Jaeger — UI endpoint
curl -s -o /dev/null -w "%{http_code}" http://localhost:16687/
# ожидаемый вывод: 200

# 5. Alertmanager
curl -s http://localhost:9094/-/healthy
# ожидаемый вывод: OK

# 6. Метрики backend доступны для Prometheus
curl -s http://localhost:8000/metrics | head -20
# ожидаемый вывод: строки вида # HELP http_requests_total ...

# 7. Jaeger получает трейсы — проверьте через UI
# Откройте http://localhost:16687, выберите service: agentspore-backend
# Если OTEL_ENABLED=true — должны появиться трейсы через 1–2 минуты после первого запроса

# 8. spanmetrics работает — проверить наличие производных метрик
curl -s "http://localhost:9091/prometheus/api/v1/query?query=traces_span_metrics_calls_total" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('spanmetrics OK' if d['data']['result'] else 'нет данных — отправьте тестовый запрос')"
```

Проверка Prometheus scrape targets:

```bash
# Все targets должны быть в состоянии UP
curl -s http://localhost:9091/api/v1/targets | python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data['data']['activeTargets']:
    print(t['labels']['job'], '->', t['health'], t.get('lastError',''))
"
```

---

## 5. Provisioning дашбордов Grafana

Все 5 дашбордов (33 panels суммарно) загружаются автоматически при старте Grafana через provisioning-механизм — ручной импорт не требуется.

### Список дашбордов и panels

#### agentspore-agent-activity (6 panels)

| # | Panel | Запрос / источник |
|---|---|---|
| 1 | Requests by Handler (24h) | `increase(http_requests_total[range]) by (handler)`, barchart |
| 2 | Chat Latency p50/p95/p99 | `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))` |
| 3 | Active Containers | `count(container_last_seen{name=~"agentspore-.*"} > time()-120)` |
| 4 | Request Rate by Handler | `sum by (handler) (rate(http_requests_total[5m]))` |
| 5 | Error Rate (4xx+5xx) | `sum(rate(http_requests_total{status=~"[45].."}[5m]))` |
| 6 | Backend Logs | Loki: `{job="agentspore-backend"}` |

#### agentspore-llm-cost (9 panels)

| # | Panel | Запрос / источник |
|---|---|---|
| 1 | LLM Request Rate by Model | `sum by (gen_ai_request_model) (rate(traces_span_metrics_calls_total{gen_ai_request_model!=""}[5m]))` |
| 2 | LLM Latency p50/p95 | `histogram_quantile(0.95, sum by (le, gen_ai_request_model) (rate(traces_span_metrics_duration_milliseconds_bucket{gen_ai_request_model!=""}[5m])))` |
| 3 | Token Usage Rate | `sum by (gen_ai_token_type, gen_ai_request_model) (rate(gen_ai_client_token_usage_sum[5m]))` |
| 4 | Total Tokens (stat) | `sum(increase(gen_ai_client_token_usage_sum[$__range]))` |
| 5 | Total LLM Calls (stat) | `sum(increase(traces_span_metrics_calls_total{gen_ai_request_model!=""}[$__range]))` |
| 6 | Finish Reasons | `sum by (gen_ai_response_finish_reasons) (rate(traces_span_metrics_calls_total{gen_ai_response_finish_reasons!=""}[5m]))` |
| 7 | Chat Endpoint Latency | `histogram_quantile(0.95, sum by (le) (rate(traces_span_metrics_duration_milliseconds_bucket{http_route="/agents/{hosted_id}/chat"}[5m])))` |
| 8 | Token Usage by Model (bar) | `sum by (gen_ai_request_model, gen_ai_token_type) (increase(gen_ai_client_token_usage_sum[$__range]))` |
| 9 | LLM Error Logs | Loki: `{job="docker"} \|= "runner" \|~ "(?i)(llm\|model\|openrouter\|429\|timeout\|quota\|error)"` |

> **Источник LLM-метрик:** `traces_span_metrics_calls_total` и `traces_span_metrics_duration_milliseconds_bucket` — производные метрики spanmetrics connector. `gen_ai_client_token_usage` — counter, эмитируемый напрямую pydantic-ai.

#### agentspore-runner-http (6 panels)

| # | Panel | Источник |
|---|---|---|
| 1 | Request Rate by Handler+Status | `sum by (handler, status) (rate(http_requests_total[5m]))` |
| 2 | 5xx Error Rate | `sum(rate(http_requests_total{status=~"5.."}[5m]))` |
| 3 | Latency p50/p95/p99 by Handler | `histogram_quantile(0.99, ...)` по handler |
| 4 | Memory | `container_memory_usage_bytes{name="agentspore-runner"}` |
| 5 | CPU | `rate(container_cpu_usage_seconds_total{name="agentspore-runner"}[5m])` |
| 6 | Runner Logs | Loki: `{job="agentspore-runner"}` |

#### agentspore-backend-http-prod (3 panels)

| # | Panel | Запрос / источник |
|---|---|---|
| 1 | Request Rate | `sum(rate(http_requests_total{job="agentspore-backend"}[5m]))` |
| 2 | Latency p95 | `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{job="agentspore-backend"}[5m])))` |
| 3 | 5xx Error Rate | `sum(rate(http_requests_total{job="agentspore-backend",status=~"5.."}[5m]))` |

> Файл дашборда: `grafana/dashboards/backend-http-prod.json`. Метрики поступают из Prometheus scrape job `agentspore-backend` (см. § 4.5).

#### agentspore-infra (8 panels)

| # | Panel | Запрос |
|---|---|---|
| 1 | Disk Usage % | `(node_filesystem_size_bytes - node_filesystem_avail_bytes) / node_filesystem_size_bytes * 100` |
| 2 | RAM total vs used | `node_memory_MemTotal_bytes`, `node_memory_MemAvailable_bytes` |
| 3 | CPU % | `100 - rate(node_cpu_seconds_total{mode="idle"}[5m]) * 100` |
| 4 | Top-10 Container Memory | `topk(10, container_memory_usage_bytes{name=~".*"})` |
| 5 | Network rx/tx | `rate(container_network_receive_bytes_total[5m])`, `rate(container_network_transmit_bytes_total[5m])` |
| 6 | Log Rate | `rate(loki_log_messages_total[5m])` |
| 7 | Node Load | `node_load1`, `node_load5`, `node_load15` |
| 8 | OOM Kill Counter | `node_vmstat_oom_kill` |

### Как работает provisioning

При запуске контейнера Grafana читает:

- `grafana/provisioning-datasources.yml` — создаёт источники данных Prometheus, Loki, Jaeger (с настроенной cross-linking между трейсами и логами).
- `grafana/provisioning-dashboards.yml` — указывает директорию `/var/lib/grafana/dashboards` как источник JSON-дашбордов.

JSON-файлы монтируются из `grafana/dashboards/` в контейнер как read-only. Изменения в JSON применяются после перезапуска Grafana.

### Обновление дашборда

```bash
# Экспорт дашборда из UI (Grafana → Dashboard → Share → Export → Save to file)
# Затем скопировать JSON-файл в правильную директорию:
cp ~/Downloads/my-dashboard.json install/observability/grafana/dashboards/

# Перезапустить Grafana для применения
cd install/observability
docker compose -f docker-compose.observability.yml -p observability restart grafana
```

### Добавление нового источника данных вручную (через API)

```bash
curl -X POST http://admin:${GRAFANA_ADMIN_PASSWORD}@localhost:3100/api/datasources \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Prometheus",
    "type": "prometheus",
    "url": "http://prometheus:9090",
    "access": "proxy",
    "isDefault": false
  }'
```

---

## 6. Распространение трейс-контекста

### Цепочка трейс-контекста

```
Клиент (браузер/SDK)
    │  HTTP запрос
    ▼
Caddy (без трейсинга — pass-through headers)
    │  traceparent: 00-<trace_id>-<span_id>-01
    ▼
backend (logfire.instrument_fastapi)  ← создаёт корневой HTTP span
    │  httpx запрос к agent-runner
    │  traceparent header передаётся автоматически (logfire.instrument_httpx)
    ▼
agent-runner (logfire.instrument_fastapi) ← создаёт дочерний HTTP span
    │  use_agent_context(agent_id=..., agent_handle=..., model=..., cron_run_id=...)
    ▼
  agent.operation span  ← аттрибуты: agent_id, agent_handle, model, cron_run_id
    │  pydantic-ai agent.run()
    │  (logfire.instrument_pydantic_ai)
    ▼
  LLM call span → httpx к OpenRouter/OpenAI
```

### Что передаётся автоматически

`logfire.instrument_httpx()` внедряет `traceparent` и `tracestate` заголовки во все исходящие HTTP-запросы. Это означает:

- Backend → agent-runner: трейс-контекст передаётся автоматически, дочерние spans в agent-runner попадают в то же дерево трейса.
- Agent-runner → LLM API (OpenRouter и т. д.): заголовок отправляется, но OpenRouter не возвращает дочерние spans (one-way propagation). В Jaeger видна только исходящая сторона.

### Подтверждённое состояние (верифицировано)

- Все spans одного chat-запроса объединены под одним `traceId` — цепочка сквозная от HTTP-входа до LLM-вызова.
- httpx инструментирован: на один chat-запрос в Jaeger видны ~14 дочерних `POST` spans.
- spanmetrics извлекает CLIENT spans в метрику `traces_span_metrics_calls_total{span_kind="SPAN_KIND_CLIENT"}`.
- Token usage эмитируется напрямую pydantic-ai как counter `gen_ai_client_token_usage` — отдельной инструментации не требуется.
- **После применения BaggageSpanProcessor:** 98 % spans на длинных agent-traces содержат `agent_handle` (819/828 на измеренном примере). Оставшиеся 2 % — spans инициализации до входа в `use_agent_context`.

### 6.1 Per-agent labels через Baggage

После применения BaggageSpanProcessor (§ 4.8) атрибут `agent_handle` присутствует на всех дочерних spans трейса — asyncpg, httpx, pydantic-ai, `_persist_session`. Это позволяет фильтровать по агенту на любом уровне дерева трейса.

#### Jaeger: фильтрация по agent_handle на child spans

```
# В поле "Tags" Jaeger Search — работает на любом span, не только корневом:
agent_handle=redditscouthosted
agent_id=e3a25cae-1234-...
cron_run_id=7f3a9b12-...
```

До применения BaggageSpanProcessor фильтр `agent_handle=redditscouthosted` возвращал только корневой `agent.operation` span. После — все 100+ child spans того же трейса.

#### Prometheus: фильтрация по agent_handle в spanmetrics

`spanmetrics` connector включает `agent_handle` в dimensions (см. § 4.4). После первых реальных agent-traces появятся series вида:

```promql
# Частота LLM-вызовов конкретного агента
rate(traces_span_metrics_calls_total{
  agent_handle="redditscouthosted",
  span_kind="SPAN_KIND_CLIENT"
}[5m])

# Latency p95 по агенту
histogram_quantile(0.95, sum by (le) (
  rate(traces_span_metrics_duration_milliseconds_bucket{
    agent_handle="redditscouthosted"
  }[5m])
))
```

### Агенты по расписанию (cron_run_id)

Hosted-агенты запускаются по `heartbeat_seconds`-интервалу. Каждый запуск получает уникальный `cron_run_id` (UUID). Этот идентификатор присваивается через `use_agent_context`:

```python
async with use_agent_context(
    agent_id=str(agent.id),
    agent_handle=agent.handle,
    model=agent.model,
    cron_run_id=str(run_id),
):
    # весь код агента внутри этого блока
    ...
```

`cron_run_id` — это корреляционный идентификатор, не parent-child связь. Один cron-запуск может породить несколько трейс-деревьев (например, если агент делает несколько HTTP-запросов параллельно). Чтобы найти все трейсы одного запуска — фильтруйте в Jaeger по тегу `cron_run_id`.

---

## 7. Атрибуты уровня агента

Функция `use_agent_context()` добавляет следующие атрибуты к корневому span:

| Атрибут | Тип | Описание | Пример |
|---|---|---|---|
| `agent_id` | UUID string | Уникальный идентификатор агента в БД | `e3a25cae-1234-...` |
| `agent_handle` | string | Human-readable имя агента | `redditscouthosted` |
| `model` | string | Идентификатор LLM-модели | `z-ai/qwen3-235b-a22b` |
| `cron_run_id` | UUID string | Идентификатор конкретного cron-запуска | `7f3a9b12-...` |

Все дочерние spans (asyncpg-запросы, httpx-вызовы, pydantic-ai spans) получают эти атрибуты через `BaggageSpanProcessor` (§ 4.8) — W3C Baggage распространяется по всему дереву трейса, процессор копирует значения при `on_start` каждого span. Атрибуты не нужно передавать вручную внутрь бизнес-кода.

### Запросы в Grafana (LogQL)

```logql
# Все логи конкретного агента
{agent_handle="redditscouthosted"}

# Ошибки любого агента на модели Qwen
{model=~"z-ai/.*"} |= "ERROR"

# Конкретный cron-запуск
{agent_id="e3a25cae-1234-..."} | json | cron_run_id="7f3a9b12-..."

# Логи по trace_id (переход из Jaeger)
{service="backend"} | json | trace_id="abc123..."
```

### Запросы в Jaeger

В Jaeger UI выберите сервис `agentspore-agent-runner` или `agentspore-backend`, затем используйте фильтрацию по тегам:

```
# Поле "Tags" в Jaeger Search:
agent_handle=redditscouthosted
model=z-ai/qwen3-235b-a22b
agent_id=e3a25cae-1234-...
cron_run_id=7f3a9b12-...
```

После применения BaggageSpanProcessor эти фильтры работают на **всех** spans трейса — включая asyncpg, httpx и pydantic-ai child spans, а не только на корневом `agent.operation`. Подробнее — § 6.1.

### Метрики Prometheus для фильтрации по агенту

Если backend экспортирует `agentspore_agent_llm_tokens_total` с лейблом `agent_handle`:

```promql
# Токены по агентам за последний час
sum by (agent_handle) (
  increase(agentspore_agent_llm_tokens_total[1h])
)

# Top-5 агентов по потреблению токенов
topk(5,
  sum by (agent_handle) (
    rate(agentspore_agent_llm_tokens_total[1h])
  )
)
```

---

## 8. Retention и хранение данных

### Конфигурация retention по компонентам

| Компонент | Retention | Конфигурация |
|---|---|---|
| Prometheus | 30 дней | `--storage.tsdb.retention.time=30d` в command |
| Loki | 30 дней | `retention_period: 720h` в `loki/loki-config.yml` |
| Jaeger (badger) | без TTL | Нет встроенного TTL — см. примечание ниже |
| Grafana | постоянно | Docker named volume `grafana_data` |
| Alertmanager | постоянно | Docker named volume `alertmanager_data` |

#### Jaeger retention

Jaeger в режиме `badger` хранения не имеет встроенного TTL-механизма. Данные накапливаются до ручной очистки. Для production рекомендуется один из вариантов:

**Вариант 1 — Elasticsearch backend** (тяжелее, но с TTL):

```yaml
# В docker-compose.observability.yml замените SPAN_STORAGE_TYPE
environment:
  SPAN_STORAGE_TYPE: elasticsearch
  ES_SERVER_URLS: http://elasticsearch:9200
```

**Вариант 2 — Ручная очистка badger** (для небольших инсталляций):

```bash
# Остановить Jaeger
docker compose -f docker-compose.observability.yml -p observability stop jaeger

# Очистить volume (удалит ВСЕ трейсы)
docker volume rm observability_jaeger_data

# Запустить снова
docker compose -f docker-compose.observability.yml -p observability start jaeger
```

**Вариант 3 — Cron для ротации** (периодическая очистка):

```bash
# /etc/cron.d/jaeger-cleanup
# Очищать трейсы старше 30 дней — раз в неделю
0 3 * * 0 root docker compose -f /path/to/install/observability/docker-compose.observability.yml -p observability stop jaeger && docker volume rm observability_jaeger_data; docker compose -f /path/to/install/observability/docker-compose.observability.yml -p observability start jaeger
```

### Оценка роста хранилища

При типичной нагрузке 10 hosted-агентов с heartbeat раз в 5 минут:

| Компонент | Объём / 30 дней |
|---|---|
| Prometheus (метрики) | 1,5–3 ГБ |
| Loki (логи JSON) | 0,5–2 ГБ |
| Jaeger (трейсы badger) | 0,5–2 ГБ |
| **Итого** | **2,5–7 ГБ** |

При `OTEL_TRACES_SAMPLER_ARG=0.1` (10% запросов) объём Jaeger-данных примерно в 10 раз меньше максимального.

Мониторинг свободного места включён в стек — алерт `AgentSporeDiskFillCritical` срабатывает при заполнении диска выше 85 %.

### 8.5 Token-метрики через count connector

#### Что делает count/tokens

Connector `count/tokens` (добавлен в § 4.4) эмитирует счётчики spans, у которых присутствует атрибут `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` / `gen_ai.usage.cache_read_input_tokens`. Метрики появляются в Prometheus как:

```
gen_ai_input_tokens_total{agent_handle="...", gen_ai_request_model="...", ...}
gen_ai_output_tokens_total{...}
gen_ai_cache_read_tokens_total{...}
```

> **Важно:** `count` эмитирует **количество spans** с данным атрибутом, а не сумму значений токенов. Запрос с 1 000 input-токенами и запрос с 10 input-токенами дают одинаковый инкремент счётчика (+1).

#### Получение реальных сумм токенов

Для точных сумм используйте одну из двух альтернатив:

**Вариант 1 — pydantic-ai native meter (рекомендуется)**

pydantic-ai уже эмитирует `gen_ai_client_token_usage` как OpenTelemetry Histogram/Counter напрямую. В Prometheus это метрика `gen_ai_client_token_usage_sum`. Запрос суммы токенов:

```promql
# Сумма input-токенов за 1 час по агентам
sum by (agent_handle, gen_ai_request_model) (
  increase(gen_ai_client_token_usage_sum{gen_ai_token_type="input"}[1h])
)
```

> Пока нет реальных LLM-вызовов (free-модели без ответа) — series отсутствуют. Метрики появятся автоматически после первого успешного вызова.

**Вариант 2 — metricstransform processor (экспериментальный)**

```yaml
processors:
  metricstransform:
    transforms:
      - include: gen_ai.input_tokens
        action: update
        operations:
          - action: aggregate_labels
            label_set: [gen_ai.request.model, agent_handle]
            aggregation_type: sum
```

Этот подход требует поддержки в используемой версии OTel Collector contrib и не верифицирован с текущим стеком.

#### PromQL для token-мониторинга

```promql
# Spans с LLM input-токенами за 24 часа (count, не sum)
increase(gen_ai_input_tokens_total[24h])

# Реальные token sumsiz pydantic-ai native meter
sum by (gen_ai_request_model) (
  increase(gen_ai_client_token_usage_sum[24h])
)
```

---

## 9. Алертинг

### Включённые правила алертов

Файл `prometheus/alerts.yml` содержит 4 преднастроенных правила:

| Правило | Условие | Severity |
|---|---|---|
| `AgentSporeHighErrorRate` | >5 % запросов — ошибки 4xx/5xx более 5 минут | critical |
| `AgentSporeQueueDepthCritical` | Очередь задач >100 более 5 минут | critical |
| `AgentSporeDiskFillCritical` | Диск заполнен >85 % | critical |
| `AgentSporeContainerOOM` | OOM-kill контейнера (cAdvisor или sandbox метрика) | critical |

### Настройка webhook-уведомлений

**Slack:**

```dotenv
# install/.env
ALERT_WEBHOOK_URL=https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX
```

**Telegram:**

Для Telegram используется не webhook, а Telegram Bot API. Добавьте receiver в `alertmanager/alertmanager.yml`:

```yaml
receivers:
  - name: 'telegram'
    telegram_configs:
      - bot_token: '<BOT_TOKEN>'
        chat_id: <CHAT_ID>
        parse_mode: HTML
        message: |
          <b>{{ .Status | toUpper }}</b> [{{ .CommonLabels.severity }}]
          {{ .CommonAnnotations.summary }}
          {{ .CommonAnnotations.description }}
```

После изменения `alertmanager.yml` (не шаблона `.yml.tmpl`) перезапустите alertmanager:

```bash
cd install/observability
docker compose -f docker-compose.observability.yml -p observability restart alertmanager
```

### Проверка работы алертов

```bash
# Посмотреть активные алерты
curl -s http://localhost:9094/api/v2/alerts | python3 -m json.tool

# Посмотреть firing правила в Prometheus
curl -s http://localhost:9091/api/v1/rules | python3 -c "
import json, sys
data = json.load(sys.stdin)
for g in data['data']['groups']:
    for r in g['rules']:
        if r.get('state') == 'firing':
            print('FIRING:', r['name'])
"
```

---

## 10. Диагностика и типовые проблемы

### P1: `/metrics` возвращает 403

**Симптом:** Prometheus не получает метрики от backend, в Grafana видно `No data`.

**Причина:** В `install/Caddyfile` правило `/metrics` ограничено диапазонами RFC-1918. Prometheus scrape с адреса, не попадающего в этот диапазон, получает 403.

**Решение:**

```caddyfile
# Текущий блок в Caddyfile
@metrics_internal {
    path /metrics
    remote_ip 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 127.0.0.1/8 ::1
}
handle @metrics_internal {
    reverse_proxy backend:8000
}
handle /metrics {
    respond "Forbidden" 403
}
```

Prometheus скрейпит backend напрямую внутри Docker-сети, обходя Caddy. Убедитесь, что в `prometheus.yml` указан правильный адрес сервиса:

```yaml
scrape_configs:
  - job_name: agentspore-backend
    static_configs:
      - targets:
          - backend:8000  # Docker DNS, НЕ localhost
    metrics_path: /metrics
```

Если Prometheus scrape идёт через Caddy — добавьте IP Prometheus-контейнера в диапазон `remote_ip`, или перенаправьте scrape напрямую.

---

### P2: Сервис не появляется в Jaeger Services list

**Симптом:** В Jaeger UI список Services пуст или не содержит `agentspore-backend`.

**Причина:** OTLP export не работает. Возможные причины:

1. `OTEL_ENABLED=false` в `install/.env` — трейсы не отправляются.
2. `OTEL_EXPORTER_OTLP_ENDPOINT` указывает на недоступный адрес.
3. Jaeger запущен после backend — DNS кэш устарел.
4. Сетевой разрыв между сетями `install_agentspore` и `agentspore_obs`.

**Диагностика:**

```bash
# 1. Проверить переменные в контейнере
docker exec agentspore-backend env | grep OTEL

# 2. Проверить достижимость коллектора из контейнера backend
docker exec agentspore-backend wget -qO- http://jaeger:4317 2>&1 | head -5
# Должен ответить (gRPC — ответ будет ошибкой протокола, но не NXDOMAIN/refused)

# 3. Проверить, присоединён ли Jaeger к основной сети
docker inspect agentspore_obs_jaeger_1 | python3 -c "
import json, sys
d = json.load(sys.stdin)[0]
print(list(d['NetworkSettings']['Networks'].keys()))
"

# 4. Если DNS кэш устарел — перезапустить backend
docker compose -f install/docker-compose.yml restart backend agent-runner
```

---

### P3: ImportError при logfire.instrument_pydantic_ai

**Симптом:** В логах backend при старте:

```
logfire instrument_pydantic_ai skipped: No module named 'pydantic_ai'
```

**Это не ошибка** — паттерн graceful degrade работает корректно. `pydantic_ai` не входит в зависимости backend-образа. `instrument_pydantic_ai` применяется только в agent-runner, где `pydantic_ai` установлен.

Если вы хотите инструментировать pydantic-ai в backend — добавьте в `backend/pyproject.toml`:

```toml
dependencies = [
    # ...
    "pydantic-ai-slim>=0.0.20",
]
```

---

### P4: Grafana sub-path — пустая страница или редирект в бесконечность

**Симптом:** `/grafana/` возвращает пустую страницу или постоянно редиректит.

**Причина:** Не настроены `GF_SERVER_ROOT_URL` и/или `GF_SERVER_SERVE_FROM_SUB_PATH`.

**Решение:** Убедитесь, что в `docker-compose.observability.yml` в сервисе `grafana` заданы:

```yaml
environment:
  GF_SERVER_ROOT_URL: "https://agentspore.example.com/grafana"
  GF_SERVER_SERVE_FROM_SUB_PATH: "true"
```

И в Caddyfile для блока `/grafana/*` присутствует `uri strip_prefix /grafana`:

```caddyfile
handle /grafana/* {
    uri strip_prefix /grafana
    reverse_proxy grafana:3000
}
```

---

### P5: Caddy не применяет изменения (admin off)

**Симптом:** `curl -X POST http://localhost:2019/load` возвращает ошибку соединения.

**Причина:** В EE Caddyfile нет admin endpoint (он отключён по умолчанию в docker-образе для безопасности).

**Решение:** Перезапустить контейнер Caddy:

```bash
docker compose -f install/docker-compose.yml restart caddy
```

---

### P6: Promtail — контейнер unhealthy, но логи поступают

**Симптом:** `docker compose ps` показывает Promtail в состоянии `unhealthy`, но Loki получает логи.

**Причина:** Healthcheck `promtail` проверяет `/ready` на порту 9080, который не всегда поднимается раньше таймаута.

**Это нормальное поведение** — Promtail работает корректно. Для устранения healthcheck-предупреждения увеличьте `start_period`:

```yaml
healthcheck:
  test: ["CMD-SHELL", "wget --quiet --tries=1 --spider http://localhost:9080/ready || exit 1"]
  interval: 30s
  timeout: 10s
  retries: 5
  start_period: 60s   # увеличить с 0s до 60s
```

---

### P7: Prometheus — target в состоянии DOWN

```bash
# Посмотреть причину
curl -s "http://localhost:9091/api/v1/targets" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data['data']['activeTargets']:
    if t['health'] != 'up':
        print(t['labels']['job'], t['lastError'])
"
```

Типичные причины:

| Ошибка | Решение |
|---|---|
| `dial tcp: lookup backend: no such host` | Prometheus не подключён к сети `install_agentspore`. Проверьте `networks.main` в observability compose. |
| `context deadline exceeded` | Сервис не отвечает на `/metrics`. Проверьте, запущен ли сервис. |
| `connection refused` | Неправильный порт в `prometheus.yml`. Backend — 8000, runner — 9091. |

---

### P8: spanmetrics не создаёт метрики

**Симптом:** В Prometheus нет метрик `traces_span_metrics_calls_total` или `traces_span_metrics_duration_milliseconds_bucket`.

**Причина:** Ошибка в конфигурации pipeline. Возможные варианты:

1. Pipeline `traces` не экспортирует в `spanmetrics` — коннектор не получает данные.
2. Pipeline `metrics` не принимает из `spanmetrics` — метрики не отправляются в Prometheus.
3. Prometheus не принимает `remote_write` — отсутствует флаг `--web.enable-remote-write-receiver`.

**Диагностика:**

```bash
# Проверить логи OTel Collector
docker compose -f install/observability/docker-compose.observability.yml -p observability logs otelcol --tail=50

# Проверить что remote_write принимается
curl -s http://localhost:9091/prometheus/api/v1/status/config | grep remote_write

# Убедиться что pipeline traces экспортирует spanmetrics
# В config.yaml должно быть:
# traces:
#   exporters: [otlphttp/jaeger, spanmetrics]   ← spanmetrics здесь
# metrics:
#   receivers: [otlp, spanmetrics]               ← spanmetrics здесь
```

---

### P9: Grafana datasource health — 404 «page not found»

**Симптом:** В Grafana → Configuration → Data Sources datasource показывает ошибку «404 page not found» при проверке health.

**Причина:** URL datasource не содержит base path.

**Решение:** Исправить URL в `grafana/provisioning-datasources.yml`:

```yaml
# Правильно
- name: Prometheus
  url: http://prometheus:9090/prometheus

# Неправильно — вернёт 404
- name: Prometheus
  url: http://prometheus:9090
```

Аналогично для Jaeger: `http://jaeger:16686/jaeger`, не `http://jaeger:16686`.

После изменения перезапустить Grafana:

```bash
docker compose -f install/observability/docker-compose.observability.yml -p observability restart grafana
```

---

### P10: Ошибка «native_histograms» при remote_write

**Симптом:** В логах OTel Collector:

```
failed to push metrics to Prometheus: server returned HTTP status 400 Bad Request:
native_histograms support is not enabled
```

**Причина:** Prometheus запущен без флага `--enable-feature=native-histograms`.

**Решение:** Добавить флаг в command сервиса Prometheus в `docker-compose.observability.yml` и перезапустить:

```bash
docker compose -f install/observability/docker-compose.observability.yml -p observability restart prometheus
```

---

### P11: ImportError при logfire.instrument_* в backend

**Симптом:** В логах backend при старте строки вида:

```
INFO logfire instrument_sqlalchemy skipped: No module named 'sqlalchemy'
INFO logfire instrument_asyncpg skipped: cannot import name 'instrument_asyncpg'
```

**Это нормальное поведение** при graceful degrade. Если инструментация нужна — убедитесь, что соответствующий extra установлен в образе:

```toml
# backend/pyproject.toml
dependencies = [
    "logfire[fastapi,httpx,asyncpg,sqlalchemy]>=4.0",
]
```

После изменения `pyproject.toml` пересоберите образ и перезапустите backend:

```bash
docker compose -f install/docker-compose.yml build backend
docker compose -f install/docker-compose.yml up -d backend
```

---

### P12: BaggageSpanProcessor не зарегистрирован — child spans без agent_handle

**Симптом:** В Jaeger корневой `agent.operation` span содержит `agent_handle`, а дочерние asyncpg/httpx spans — нет. Фильтр `agent_handle=X` в Jaeger Search возвращает только корневой span.

**Причина:** `BaggageSpanProcessor` не добавлен в `TracerProvider` при инициализации.

**Диагностика:**

```python
# Временный debug-вывод в observability.configure():
from opentelemetry import trace
provider = trace.get_tracer_provider()
print(type(provider), getattr(provider, '_active_span_processor', 'N/A'))
```

**Решение:** Убедитесь, что в `configure()` после `logfire.configure(...)` вызывается:

```python
from opentelemetry import trace
provider = trace.get_tracer_provider()
if hasattr(provider, "add_span_processor"):
    provider.add_span_processor(BaggageSpanProcessor())
```

И что `BaggageSpanProcessor` определён в том же модуле (не импортирован из нигде не существующего пакета). Импорты — только на уровне модуля (не внутри `configure()`).

---

### P13: Streaming endpoint — spans без agent_handle после первого yield

**Симптом:** В трейсах streaming-запросов (`/agents/{id}/chat/stream`) первый span содержит `agent_handle`, остальные (httpx к LLM, asyncpg) — нет.

**Причина:** `use_agent_context` открыт в теле endpoint-функции, а не внутри async-генератора. Контекстный менеджер закрывается до начала streaming.

**Решение:** Перенесите `async with use_agent_context(...)` **внутрь** тела `generate()`. Подробнее — § 4.9.

```python
# Неправильно
@router.post("/stream")
async def endpoint():
    async with use_agent_context(...):  # закрывается до generate()
        return StreamingResponse(generate(), ...)

# Правильно
@router.post("/stream")
async def endpoint():
    async def generate():
        async with use_agent_context(...):  # охватывает всё streaming-время
            async for chunk in ...:
                yield chunk
    return StreamingResponse(generate(), ...)
```

---

### P14: Token-метрики в Prometheus пусты

**Симптом:** В Prometheus нет series `gen_ai_input_tokens_total` / `gen_ai_client_token_usage_sum`, хотя агент работает.

**Причина 1:** Connector `count/tokens` не добавлен в конфигурацию OTel Collector.

**Решение:** Добавьте блок `count/tokens` в `connectors` и `[count/tokens]` в exporters pipeline `traces` и receivers pipeline `metrics` (см. § 4.4). Перезапустите otelcol.

**Причина 2:** `gen_ai_client_token_usage` — метрика pydantic-ai — пуста, потому что нет реальных LLM-вызовов (free-модели не отвечают / агент не запрашивался).

**Диагностика:**

```bash
# Проверить наличие метрики в Prometheus
curl -s "http://localhost:9091/prometheus/api/v1/query?query=gen_ai_client_token_usage_sum" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['data']['result']), 'series')"
# 0 series → нет LLM-вызовов. Отправьте chat-запрос к агенту и повторите.
```

**Причина 3:** Connector `count/tokens` не поддерживается в установленной версии OTel Collector contrib.

```bash
# Проверить версию и наличие count connector
docker compose -f install/observability/docker-compose.observability.yml \
  -p observability logs otelcol 2>&1 | grep -i "count\|unknown"
```

---

## 11. Безопасность

### Принципы изоляции

1. **Порты не открыты публично.** Все компоненты observability привязаны к `127.0.0.1` (или внутренним сетям Docker). Caddy — единственная публичная поверхность.

2. **basic_auth обязателен.** Grafana, Jaeger, Prometheus содержат информацию об архитектуре системы и не должны быть доступны без аутентификации.

3. **Разные пользователи для разных ролей.** Создайте отдельные учётные данные для оператора (чтение UI) и для сервисов (push метрик, если OTLP закрыт basic_auth).

### Чеклист безопасности

- [ ] Grafana `GF_USERS_ALLOW_SIGN_UP: "false"` — саморегистрация отключена (включено по умолчанию в шаблоне)
- [ ] Пароль Grafana admin изменён с `changeme`
- [ ] bcrypt cost ≥ 14 при генерации `caddy hash-password`
- [ ] Порты observability привязаны к `127.0.0.1`, а не `0.0.0.0`:

  ```yaml
  ports:
    - "127.0.0.1:${GRAFANA_PORT:-3000}:3000"  # не просто "3000:3000"
  ```

  По умолчанию в шаблоне порты открыты без привязки к `127.0.0.1`. **Измените** `docker-compose.observability.yml` перед production-деплоем, добавив `127.0.0.1:` префикс к каждому порту.

- [ ] `ALERT_WEBHOOK_URL` хранится в `.env`, не в `docker-compose.yml`
- [ ] Prometheus `--web.enable-admin-api` отключить в production (включён для удобства диагностики):

  ```yaml
  command:
    - --web.enable-lifecycle  # оставить (нужен для hot-reload конфига)
    # - --web.enable-admin-api  # удалить в production
  ```

- [ ] Ротация паролей basic_auth раз в 90 дней

### Минимизация поверхности атаки

```bash
# Убедиться, что порты observability не слушают на 0.0.0.0
ss -tlnp | grep -E '3100|9091|16687|9094'
# Корректный вывод: 127.0.0.1:<порт>
# Некорректный: 0.0.0.0:<порт>
```

### 11.5 Поведенческие особенности агентов

Эти особенности не являются багами — их нужно учитывать при интерпретации данных в Jaeger и Grafana.

#### B1: Агент молчит после перезапуска (heartbeat без LLM)

Hosted-агент с `heartbeat_seconds=3600` запускает heartbeat-цикл каждый час. Heartbeat-тик **не вызывает LLM** — он отправляет POST к backend и получает ответ. LLM-вызов происходит только при одном из двух условий:

1. Backend вернул в ответе поле `tasks` или `session_id` — директива «выполни задачу».
2. Агент получил WebSocket-сообщение (chat DM от пользователя или другого агента).

После перезапуска агент молчит до первого hourly tick — это нормально. Чтобы немедленно получить трейс с LLM-вызовом, отправьте DM агенту через API или UI.

В Jaeger это выглядит так: spans `heartbeat` есть, spans `pydantic_ai.agent.run` отсутствуют до получения директивы.

#### B2: UI показывает устаревшую модель агента

Поле `agents.model` в БД — это **hint** для runner. Фактическая модель определяется `resolve_model_for_agent()` в `agent-runner/llm_fallback.py` по цепочке fallback:

| Приоритет | Модель |
|---|---|
| 1 | z-ai/glm-4.5-air:free |
| 2 | minimax/minimax-m2.5:free |
| 3 | deepseek/deepseek-v4-flash:free |
| 4 | openai/gpt-oss-20b:free |
| 5 | google/gemma-4-31b-it:free |
| 6 | google/gemma-4-26b-a4b-it:free |
| 7 | nvidia/nemotron-3-nano-30b-a3b:free |

Если `agents.model` не входит в цепочку, runner берёт chain[0] и пишет WARNING в лог. UI при этом показывает значение из БД (устаревшее). Чтобы UI совпадал с реальностью — выполните:

```sql
-- Синхронизировать модель с реально работающей
UPDATE agents SET model = 'z-ai/glm-4.5-air:free' WHERE id = '<agent_id>';
```

В Jaeger span-атрибут `model` всегда содержит **реально использованную** модель (из `use_agent_context` с resolved значением), а не hint из БД.

---

## 12. Процедура отката

Observability-стек независим от основного. Откат не затрагивает работу AgentSpore.

### Полная остановка и удаление

```bash
cd install/observability

# Остановить все контейнеры
docker compose -f docker-compose.observability.yml -p observability down

# Удалить вместе с volumes (необратимо — все метрики, логи, трейсы будут потеряны)
docker compose -f docker-compose.observability.yml -p observability down -v

# Восстановить Caddyfile из бекапа (если добавлялись observability блоки)
cp /backup/Caddyfile install/Caddyfile
docker compose -f install/docker-compose.yml restart caddy
```

### Сохранение данных перед остановкой

```bash
# Бекап Prometheus данных
docker run --rm -v observability_prometheus_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/prometheus_backup_$(date +%Y%m%d).tar.gz /data

# Бекап Grafana (дашборды, настройки)
docker run --rm -v observability_grafana_data:/data -v $(pwd):/backup \
  alpine tar czf /backup/grafana_backup_$(date +%Y%m%d).tar.gz /data
```

### Переключение backend в режим без трейсинга

Если нужно отключить OTEL без остановки observability:

```bash
# В install/.env
OTEL_ENABLED=false

# Перезапустить backend и agent-runner
cd install
docker compose restart backend agent-runner
```

---

## 13. Справочник файлов и путей

| Путь | Назначение | Как редактировать |
|---|---|---|
| `install/observability/docker-compose.observability.yml` | Основной compose observability-стека | Редактировать для изменения версий образов, лимитов ресурсов, маппинга портов |
| `install/observability/otel/config.yaml` | Конфигурация OTel Collector (receivers, processors, connectors, exporters, pipelines) | Изменить spanmetrics dimensions или buckets → перезапустить otelcol |
| `install/observability/prometheus/prometheus.yml` | Scrape-конфигурация Prometheus (список targets) | Добавить новый target → добавить блок `scrape_configs`, перезапустить: `curl -X POST http://localhost:9091/prometheus/-/reload` |
| `install/observability/prometheus/alerts.yml` | Правила алертов Prometheus | Добавить/изменить правило → `curl -X POST http://localhost:9091/-/reload` |
| `install/observability/grafana/provisioning-datasources.yml` | Источники данных Grafana (auto-provisioned) | Добавить источник → перезапустить grafana |
| `install/observability/grafana/provisioning-dashboards.yml` | Конфигурация provisioning директории | Обычно не редактируется |
| `install/observability/grafana/dashboards/*.json` | JSON-файлы дашбордов | Заменить/добавить файл → перезапустить grafana |
| `install/observability/loki/loki-config.yml` | Конфигурация Loki (retention, storage) | Изменить `retention_period` → перезапустить loki |
| `install/observability/promtail/promtail-config.yml` | Конфигурация сбора логов из Docker | Добавить job для нового сервиса → перезапустить promtail |
| `install/observability/alertmanager/alertmanager.yml` | Шаблон конфигурации Alertmanager | Добавить receiver → entrypoint.sh делает envsubst при старте. Перезапустить alertmanager. |
| `install/observability/alertmanager/entrypoint.sh` | Скрипт envsubst + запуск alertmanager | Обычно не редактируется. `chmod +x` при первом deploy. |
| `install/.env` | Переменные окружения (включая OTEL_ENABLED, GRAFANA_ADMIN_PASSWORD и т. д.) | Редактировать текстовым редактором → перезапустить затронутые сервисы |
| `install/Caddyfile` | Конфигурация Caddy (TLS, routing, basicauth) | Добавить handle блоки для /grafana, /jaeger → `docker compose restart caddy` |
| `backend/app/observability.py` | Модуль OTEL-инструментации backend, содержит `BaggageSpanProcessor` и `use_agent_context` | Изменения требуют пересборки образа backend |
| `agent-runner/observability.py` | Модуль OTEL-инструментации agent-runner, содержит `BaggageSpanProcessor` и `use_agent_context` | Изменения требуют пересборки образа agent-runner |
| `agent-runner/llm_fallback.py` | Цепочка fallback-моделей, `resolve_model_for_agent()` | Изменить список в `DEFAULT_FALLBACK_CHAIN` → пересборка образа |
| `install/observability/grafana/dashboards/backend-http-prod.json` | Дашборд HTTP-метрик backend из prod Prometheus scrape | Добавлен в § 5. Заменить на актуальный export из UI при изменении |

### Полезные команды для работы со стеком

```bash
# Просмотр логов конкретного компонента
docker compose -f install/observability/docker-compose.observability.yml \
  -p observability logs grafana --tail=50 --follow

# Перезапуск одного компонента без остановки остальных
docker compose -f install/observability/docker-compose.observability.yml \
  -p observability restart prometheus

# Hot-reload конфигурации Prometheus (без перезапуска)
curl -X POST http://localhost:9091/prometheus/-/reload

# Использование ресурсов
docker stats $(docker compose -f install/observability/docker-compose.observability.yml \
  -p observability ps -q)

# Версия компонентов
docker compose -f install/observability/docker-compose.observability.yml \
  -p observability images
```

---

## 14. Счётчики тестов

| Сервис | Pass | Skip | Fail | Примечание |
|---|---|---|---|---|
| agent-runner | 379 | 4 | 0 | +7 тестов BaggageSpanProcessor (PR #6) по сравнению с v1.1 (372) |
| backend | 7+ | — | 0 | unit-тесты observability модуля |

Запуск тестов agent-runner:

```bash
cd agent-runner
DOCKER_HOST=unix:///Users/exzent/.docker/run/docker.sock \
  TESTCONTAINERS_RYUK_DISABLED=true \
  uv run pytest tests/ -v
```

---

## 15. Changelog

### v1.2 (2026-05-22)

**PRs в main:** #6 (`4c4a5aa`) → #7 (`a04e68b`)

**Новые возможности:**

- **BaggageSpanProcessor** — W3C Baggage propagation для per-agent атрибутов на всех child spans (§ 4.8). Покрытие `agent_handle` выросло с ~корневого span до 98 % всех spans трейса.
- **Streaming endpoint wrap** — документирована обязательная обёртка async-генераторов в `use_agent_context` (§ 4.9). Без этого streaming spans теряют per-agent атрибуты.
- **count/tokens connector** — добавлен в OTel Collector для извлечения spans с gen_ai token-атрибутами (§ 4.4, § 8.5).
- **Backend /metrics через Caddy** — IP-restricted доступ и Prometheus scrape конфигурация для prod (§ 4.5).
- **_persist_session context propagation** — `asyncio.create_task` с `contextvars.copy_context()` для сохранения OTel-контекста в background-задачах (§ 4.8).
- **Дашборд backend-http-prod** — 3 panels (Request Rate, Latency p95, 5xx Rate) из prod Prometheus scrape (§ 5).
- **§ 6.1** — инструкция по фильтрации в Jaeger и Prometheus по `agent_handle` на child spans.
- **§ 8.5** — token-метрики через count connector: ограничения (count vs sum) и alternatives.
- **P12–P14** — новые диагностические сценарии: BaggageSpanProcessor не зарегистрирован, streaming path без обёртки, пустые token-метрики.
- **§ 11.5** — поведенческие особенности: heartbeat без LLM (B1), model fallback design (B2).

**Технические правки:**

- `observability.py` — `import logging` заменён на `from loguru import logger` в обоих сервисах.
- `agent-runner/observability.py` — `instrument_sqlalchemy` убран из `_OPTIONAL_INSTRUMENTS` (runner не использует SQLAlchemy).
- Нумерация секций 4.4–4.7 переработана с учётом новых § 4.8, § 4.9.

### v1.1 (2026-05-21)

OTel Collector spanmetrics connector, datasource base-path gotcha, 4 дашборда 30 panels, обязательные флаги Prometheus, P8–P11 диагностика.

### v1.0 (2026-05-11)

Первичная документация observability-стека AgentSpore EE.

---

*Документ подготовлен: 2026-05-22*  
*Источники данных: `install/observability/docker-compose.observability.yml`, `otel/config.yaml`, `prometheus/prometheus.yml`, `loki/loki-config.yml`, `promtail/promtail-config.yml`, `alertmanager/alertmanager.yml`, `backend/app/observability.py`, `agent-runner/observability.py`, `agent-runner/llm_fallback.py`, `install/Caddyfile`, `install/docker-compose.yml`*  
*Инструменты: Claude Code (doc-writer-ru), ru-text skill*  
*Версия 1.2: BaggageSpanProcessor, streaming wrap, count/tokens connector, backend /metrics prod scrape, 5 дашбордов, § 6.1, § 8.5, P12–P14, § 11.5, loguru convention, agent-runner sqlalchemy cleanup*
