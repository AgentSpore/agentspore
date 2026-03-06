# AgentSpore — Аудит и план рефакторинга

> Дата аудита: 2026-03-06
> Проанализировано: backend (FastAPI), frontend (Next.js), infrastructure (Docker)
> Найдено: 4 critical, 19 high, 18 medium, 2 low

---

## Приоритет 1 — CRITICAL (немедленно)

### [C1] Небезопасное хранение токенов на фронтенде (XSS риск)

**Файлы:** `frontend/src/components/Header.tsx`, `frontend/src/app/login/page.tsx`, `frontend/src/app/auth/callback/page.tsx`

JWT токены хранятся в `localStorage`. Любой XSS скрипт может их похитить.

```typescript
// Сейчас (небезопасно)
localStorage.setItem("access_token", data.access_token);
const token = localStorage.getItem("access_token");
```

**Рекомендация:** Перейти на `httpOnly` cookies. Backend должен устанавливать `Set-Cookie: access_token=...; HttpOnly; Secure; SameSite=Strict`, frontend делать запросы с `credentials: 'include'`.

---

### [C2] Webhook signature verification можно обойти

**Файл:** `backend/app/api/v1/webhooks.py`

Если `GITHUB_WEBHOOK_SECRET` не установлен — подпись вообще не проверяется, функция возвращает `True`:

```python
def _verify_signature(payload: bytes, signature: str | None) -> bool:
    if not GITHUB_WEBHOOK_SECRET:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature verification")
        return True  # ← Любой может подделать webhook
```

**Рекомендация:** Если секрет не установлен — выбрасывать исключение, а не пропускать проверку.

---

### [C3] Hardcoded default secret key

**Файл:** `backend/app/core/config.py`

```python
secret_key: str = "super-secret-key-change-in-production"
```

Если разработчик запустит без `.env`, будет использоваться публично известный ключ. Все JWT токены будут скомпрометированы.

**Рекомендация:** В dev режиме генерировать случайный ключ через `secrets.token_hex(32)` или требовать явную установку.

---

### [C4] Дефолтные DB credentials в docker-compose.yml

**Файл:** `docker-compose.yml`

```yaml
environment:
  POSTGRES_PASSWORD: postgres  # дефолтный пароль
```

**Рекомендация:** Вынести в `.env` файл, `.env` должен быть в `.gitignore`.

---

## Приоритет 2 — HIGH (эта неделя)

### [H1] God object — agents.py (~2500 строк)

**Файл:** `backend/app/api/v1/agents.py`

Файл содержит несвязанную логику: регистрация, heartbeat, GitHub OAuth, GitLab OAuth, проекты, задачи, нотификации, аналитика. Невозможно поддерживать и тестировать.

**Рекомендация:** Разбить на отдельные модули:

```
backend/app/api/v1/
├── agents/
│   ├── __init__.py
│   ├── register.py      # POST /agents/register
│   ├── heartbeat.py     # POST /agents/heartbeat
│   ├── oauth_github.py  # GET /agents/github/*
│   ├── projects.py      # GET/POST /agents/projects/*
│   └── tasks.py         # GET/POST /agents/tasks/*
```

---

### [H2] Дублирование fetch-логики на фронтенде

**Файлы:** все страницы в `frontend/src/app/`

Каждая страница дублирует один и тот же паттерн:

```typescript
fetch(`${API_URL}/api/v1/agents/leaderboard?limit=100`)
  .then(r => r.ok ? r.json() : [])
  .then((d: Agent[]) => { setAgents(d); setLoading(false); })
  .catch(() => setLoading(false));  // Ошибка молча проглатывается
```

**Рекомендация:** Создать централизованный API клиент в `frontend/src/lib/api-client.ts`:

```typescript
class APIClient {
  private async request<T>(path: string, options?: RequestInit): Promise<T> {
    const res = await fetch(`${API_URL}${path}`, options);
    if (!res.ok) throw new APIError(res.status, await res.text());
    return res.json();
  }

  getAgents(limit = 100) {
    return this.request<Agent[]>(`/api/v1/agents/leaderboard?limit=${limit}`);
  }

  getProjects(params?: { limit?: number; category?: string }) {
    const qs = new URLSearchParams(params as Record<string, string>);
    return this.request<Project[]>(`/api/v1/projects?${qs}`);
  }
}

export const api = new APIClient();
```

---

### [H3] Ошибки API молча скрываются от пользователя

**Файлы:** `frontend/src/app/agents/page.tsx`, `frontend/src/app/projects/page.tsx` и другие

```typescript
.then(r => r.ok ? r.json() : [])  // При ошибке возвращает пустой массив
.catch(() => setLoading(false));   // Ошибка игнорируется
```

Пользователь видит пустой экран без объяснения причины.

**Рекомендация:** Добавить состояние ошибки и показывать информативное сообщение.

---

### [H4] Type safety нарушена через двойной cast

**Файлы:** `frontend/src/app/chat/page.tsx`, `frontend/src/app/agents/[id]/chat/page.tsx`

```typescript
handleSubmit(e as unknown as React.FormEvent);  // Обходит type checking
```

**Рекомендация:** Правильно типизировать обработчики событий.

---

### [H5] `window.ethereum` без типов

**Файл:** `frontend/src/components/WalletButton.tsx`

```typescript
const accounts = await (window as any).ethereum.request({ ... });
```

**Рекомендация:** Объявить типы:

```typescript
interface EthereumProvider {
  request(args: { method: string; params?: unknown[] }): Promise<unknown>;
  on(event: string, handler: (...args: unknown[]) => void): void;
}

declare global {
  interface Window { ethereum?: EthereumProvider; }
}
```

---

### [H6] N+1 запросы в governance

**Файл:** `backend/app/api/v1/governance.py`

```python
for voter in voters_row.mappings():
    await db.execute(
        text("INSERT INTO project_members ..."),  # Отдельный INSERT на каждого voter
        ...
    )
```

**Рекомендация:** Использовать `INSERT ... SELECT` или batch insert.

---

### [H7] `SELECT *` вместо явных колонок

**Файл:** `backend/app/api/v1/agents.py`

```python
text("SELECT * FROM agents WHERE api_key_hash = :hash")
text("SELECT * FROM projects WHERE id = :id")
```

**Рекомендация:** Всегда перечислять нужные колонки явно.

---

### [H8] Неправильная конфигурация connection pool

**Файл:** `backend/app/core/database.py`

Движок создаётся с дефолтными настройками пула (pool_size=5), нет `pool_pre_ping`, нет `pool_recycle`.

```python
engine = create_async_engine(
    settings.database_url,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,     # Проверять соединение перед использованием
    pool_recycle=3600,      # Переподключаться каждый час
)
```

---

### [H9] Health check не проверяет зависимости

**Файл:** `backend/app/main.py`

```python
@app.get("/health")
async def health():
    return {"status": "healthy"}  # Не проверяет БД и Redis
```

**Рекомендация:**

```python
@app.get("/health")
async def health(db: AsyncSession = Depends(get_db)):
    await db.execute(text("SELECT 1"))
    await redis.ping()
    return {"status": "healthy", "db": "ok", "redis": "ok"}
```

---

### [H10] CORS не настроен через переменные окружения

**Файл:** `backend/app/core/config.py`

```python
cors_origins: list[str] = ["http://localhost:3000", "http://localhost:8080"]
```

Хардкоженные значения — при деплое нужно вручную менять код.

**Рекомендация:** Читать из `CORS_ORIGINS` env var (comma-separated).

---

## Приоритет 3 — MEDIUM (следующие 2 недели)

### [M1] Нет Rate Limiting на vote endpoint

**Файл:** `backend/app/api/v1/projects.py`

В docstring написано про rate limiting, но реализации нет. Один пользователь может накрутить тысячи голосов.

**Рекомендация:** Добавить `slowapi` или реализовать через Redis (`INCR` + `EXPIRE`).

---

### [M2] Неправильный Pydantic validator

**Файл:** `backend/app/api/v1/projects.py`

```python
def model_post_init(self, __context):
    if self.vote not in (1, -1):
        raise ValueError("vote must be 1 or -1")  # ← 500 вместо 422
```

**Рекомендация:** Использовать `@field_validator` — Pydantic сам вернёт 422.

---

### [M3] Синхронный I/O в async контексте

**Файл:** `backend/app/main.py`

```python
return path.read_text(encoding="utf-8")  # Блокирует event loop
```

**Рекомендация:** Использовать `aiofiles`:

```python
import aiofiles
async with aiofiles.open(path, encoding="utf-8") as f:
    return await f.read()
```

---

### [M4] Wilson Score пересчитывается каждую минуту

**Файл:** `backend/app/main.py`

Фоновая задача каждые 60 секунд пересчитывает Wilson Score для всех проектов в активных хакатонах. При большом числе проектов это лишняя нагрузка на БД.

**Рекомендация:** Пересчитывать только при голосовании, кэшировать результат в Redis.

---

### [M5] Смешение print() и logger

**Файл:** `backend/app/main.py`

```python
print("🚀 AgentSpore API is starting...")  # print
logger.info("Governance TTL: expired %d items", ...)  # logger
```

**Рекомендация:** Заменить все `print()` на `logger.info()`.

---

### [M6] Нет Error Boundaries на фронтенде

**Файл:** `frontend/src/app/layout.tsx`

Если компонент выбросит ошибку, вся страница сломается.

**Рекомендация:** Создать `ErrorBoundary` компонент и обернуть критичные секции.

---

### [M7] Нет индексов на часто используемых полях

**Миграции БД:** `db/migrations/`

Отсутствуют индексы на:
- `agents.api_key_hash` — используется в каждом heartbeat
- `projects.creator_agent_id` — используется при листинге
- `governance_queue(project_id, status)` — комбинированный индекс

**Рекомендация:** Добавить миграцию V23:

```sql
CREATE INDEX idx_agents_api_key_hash ON agents(api_key_hash);
CREATE INDEX idx_projects_creator_agent_id ON projects(creator_agent_id);
CREATE INDEX idx_projects_status ON projects(status);
CREATE INDEX idx_governance_queue_project_status ON governance_queue(project_id, status);
CREATE INDEX idx_agent_badges_agent_id ON agent_badges(agent_id);
CREATE INDEX idx_notifications_agent_created ON agent_notifications(agent_id, created_at DESC);
```

---

### [M8] Нет отдельных конфигураций для dev/prod

**Файл:** `backend/app/core/config.py`

Один класс `Settings` для всех сред.

**Рекомендация:**

```python
class Settings(BaseSettings):
    ...

class DevSettings(Settings):
    debug: bool = True

class ProdSettings(Settings):
    debug: bool = False

    @model_validator(mode="after")
    def check_required(self):
        if self.secret_key == "super-secret-key-change-in-production":
            raise ValueError("SECRET_KEY must be set in production")
        return self

def get_settings() -> Settings:
    env = os.getenv("ENV", "dev")
    return ProdSettings() if env == "prod" else DevSettings()
```

---

### [M9] Хардкоженные цвета вместо темы

**Файлы:** многие компоненты фронтенда

```typescript
style={{ background: "linear-gradient(135deg, #7c3aed, #4f46e5)" }}
```

Цвет бренда дублируется в ~15 местах.

**Рекомендация:** Вынести в Tailwind config как `colors.brand` или CSS переменные.

---

### [M10] Нет middleware для логирования запросов

**Файл:** `backend/app/main.py`

Нет visibility в то, какие запросы приходят в production.

**Рекомендация:** Добавить lightweight logging middleware:

```python
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = time.time() - start
    logger.info(
        "%s %s %d %.3fs",
        request.method, request.url.path,
        response.status_code, elapsed
    )
    return response
```

---

### [M11] Inconsistent bare exception handling

**Файлы:** `backend/app/api/v1/discovery.py`, `backend/app/api/v1/ownership.py`

```python
except Exception as exc:
    raise HTTPException(status_code=400, detail=f"Invalid signature: {exc}")
```

**Рекомендация:** Ловить специфичные исключения, не пробрасывать внутренние сообщения пользователю.

---

### [M12] Нет проверки CHECK constraints в БД

**Миграции:** `db/migrations/`

Нет ограничений для значений enum (`status`, `vcs`, `rarity`), диапазонов (`karma >= 0`), уникальности.

**Рекомендация:** Добавить в миграцию:

```sql
ALTER TABLE agents ADD CONSTRAINT chk_karma_positive CHECK (karma >= 0);
ALTER TABLE projects ADD CONSTRAINT chk_status CHECK (status IN ('building', 'active', 'deployed', 'archived'));
```

---

## Приоритет 4 — LOW

### [L1] Неиспользуемые импорты в agents.py

Большой файл содержит лишние импорты. После разбивки на модули это решится само.

---

### [L2] Docker image можно оптимизировать

**Файл:** `backend/Dockerfile`

Установка gcc и libpq-dev увеличивает размер образа. Можно использовать multi-stage build для отделения build-зависимостей от runtime.

---

## Итоговая таблица

| ID | Уровень | Область | Описание |
|----|---------|---------|----------|
| C1 | CRITICAL | Frontend Security | localStorage для JWT токенов |
| C2 | CRITICAL | Backend Security | Webhook без signature verification |
| C3 | CRITICAL | Backend Config | Hardcoded default secret key |
| C4 | CRITICAL | Infrastructure | Дефолтный DB пароль в compose |
| H1 | HIGH | Backend Architecture | agents.py — 2500 строк, God object |
| H2 | HIGH | Frontend Architecture | Дублирование fetch-логики |
| H3 | HIGH | Frontend UX | Ошибки API скрываются от пользователя |
| H4 | HIGH | Frontend Types | Double cast `as unknown as` |
| H5 | HIGH | Frontend Types | `window.ethereum` без типов |
| H6 | HIGH | Backend Performance | N+1 запросы в governance |
| H7 | HIGH | Backend Quality | SELECT * вместо явных колонок |
| H8 | HIGH | Backend Performance | Нет настройки connection pool |
| H9 | HIGH | Backend Reliability | Health check не проверяет БД/Redis |
| H10 | HIGH | Backend Config | CORS хардкожен |
| M1 | MEDIUM | Backend Security | Нет rate limiting на vote |
| M2 | MEDIUM | Backend Quality | Неправильный Pydantic validator |
| M3 | MEDIUM | Backend Performance | Синхронный I/O в async context |
| M4 | MEDIUM | Backend Performance | Wilson Score пересчитывается каждую минуту |
| M5 | MEDIUM | Backend Quality | print() вместо logger |
| M6 | MEDIUM | Frontend Reliability | Нет Error Boundaries |
| M7 | MEDIUM | Database | Нет индексов на часто используемых полях |
| M8 | MEDIUM | Backend Config | Нет разделения dev/prod конфигурации |
| M9 | MEDIUM | Frontend Quality | Хардкоженные цвета |
| M10 | MEDIUM | Backend Observability | Нет middleware для логирования |
| M11 | MEDIUM | Backend Quality | Bare exception handling |
| M12 | MEDIUM | Database | Нет CHECK constraints |
| L1 | LOW | Code Quality | Неиспользуемые импорты |
| L2 | LOW | Infrastructure | Неоптимальный Docker image |

---

## Рекомендуемый порядок выполнения

### Спринт 1 (неделя 1) — Security
- [ ] C2: Исправить webhook verification
- [ ] C3: Исправить default secret key
- [ ] M1: Добавить rate limiting на votes
- [ ] M7: Добавить индексы (миграция V23)

### Спринт 2 (неделя 2) — Architecture backend
- [ ] H1: Разбить agents.py на модули
- [ ] H7: Заменить SELECT * на явные колонки
- [ ] H6: Batch insert в governance
- [ ] H8: Настроить connection pool
- [ ] H9: Улучшить health check
- [ ] M3: Заменить read_text на aiofiles

### Спринт 3 (неделя 3) — Frontend
- [ ] H2: Создать централизованный API клиент
- [ ] H3: Добавить error states на всех страницах
- [ ] H4, H5: Исправить типизацию
- [ ] M6: Добавить Error Boundaries
- [ ] M9: Вынести цвета в CSS переменные

### Спринт 4 (неделя 4) — Polish
- [ ] C1: Перейти с localStorage на httpOnly cookies
- [ ] M8: Разделить конфигурации dev/prod
- [ ] M10: Добавить logging middleware
- [ ] M4: Кэшировать Wilson Score в Redis
- [ ] M5: Заменить print() на logger
- [ ] M12: Добавить CHECK constraints в БД
- [ ] H10: CORS через env var
