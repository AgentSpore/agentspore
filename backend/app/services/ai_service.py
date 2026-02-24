"""AI сервис для генерации идей и прототипов через OpenRouter."""

import json
from openai import AsyncOpenAI

from app.core.config import get_settings

settings = get_settings()


class AIService:
    """Сервис для работы с AI через OpenRouter."""

    def __init__(self):
        # OpenRouter совместим с OpenAI SDK через base_url
        self.client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
        self.model = settings.llm_model

    async def discover_problems(self, category: str | None = None) -> list[dict]:
        """Найти проблемы для генерации идей стартапов."""
        prompt = f"""Ты эксперт по поиску проблем для стартапов.
        
Твоя задача: найти 5 реальных проблем, которые люди обсуждают в интернете, 
и которые можно решить с помощью технологий.

{"Фокусируйся на категории: " + category if category else "Охвати разные категории."}

Для каждой проблемы укажи:
1. Краткое описание проблемы
2. Где она обсуждается (Reddit, Twitter, форумы)
3. Потенциальная аудитория
4. Почему это важно решить

Ответ в формате JSON массива:
[
  {{
    "problem": "описание проблемы",
    "source": "где обсуждается",
    "audience": "целевая аудитория",
    "importance": "почему важно"
  }}
]"""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            response_format={"type": "json_object"},
        )

        try:
            content = response.choices[0].message.content
            data = json.loads(content)
            return data.get("problems", data) if isinstance(data, dict) else data
        except (json.JSONDecodeError, AttributeError):
            return []

    async def generate_idea_from_problem(self, problem: str) -> dict:
        """Сгенерировать идею стартапа на основе проблемы."""
        prompt = f"""Ты опытный предприниматель и product manager.

На основе следующей проблемы сгенерируй идею стартапа:

ПРОБЛЕМА: {problem}

Создай концепцию стартапа с:
1. Название (креативное, запоминающееся)
2. Описание решения (как решает проблему)
3. Ключевые фичи (3-5 штук)
4. Бизнес-модель (как зарабатывать)
5. Категория (SaaS, Mobile, Marketplace, etc.)

Ответ в формате JSON:
{{
  "title": "название",
  "problem": "переформулированная проблема",
  "solution": "описание решения",
  "features": ["фича 1", "фича 2", ...],
  "business_model": "модель монетизации",
  "category": "категория"
}}"""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.8,
            response_format={"type": "json_object"},
        )

        try:
            content = response.choices[0].message.content
            return json.loads(content)
        except (json.JSONDecodeError, AttributeError):
            return {}

    async def generate_prototype_html(self, idea_title: str, idea_solution: str) -> str:
        """Сгенерировать HTML прототип для идеи."""
        prompt = f"""Создай интерактивный HTML прототип для стартапа.

НАЗВАНИЕ: {idea_title}
ОПИСАНИЕ: {idea_solution}

Создай одностраничный HTML с:
1. Современным дизайном (используй Tailwind CSS через CDN)
2. Хедер с логотипом и навигацией
3. Hero секция с описанием продукта
4. 3 карточки с ключевыми фичами
5. CTA кнопка "Попробовать"
6. Футер

Код должен быть полностью самодостаточным (все стили inline или через CDN).
Добавь базовую интерактивность на JavaScript.

Ответь ТОЛЬКО HTML кодом, без markdown."""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )

        content = response.choices[0].message.content
        # Очищаем от markdown если есть
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        return content

    async def improve_idea_description(self, problem: str, solution: str) -> dict:
        """Улучшить описание идеи."""
        prompt = f"""Улучши описание идеи стартапа.

ПРОБЛЕМА (текущее описание): {problem}
РЕШЕНИЕ (текущее описание): {solution}

Сделай описания:
1. Более чёткими и конкретными
2. С акцентом на ценность для пользователя
3. С измеримыми преимуществами

Ответ в формате JSON:
{{
  "problem": "улучшенное описание проблемы",
  "solution": "улучшенное описание решения"
}}"""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            response_format={"type": "json_object"},
        )

        try:
            content = response.choices[0].message.content
            return json.loads(content)
        except (json.JSONDecodeError, AttributeError):
            return {"problem": problem, "solution": solution}

    async def regenerate_prototype_with_features(
        self,
        idea_title: str,
        idea_solution: str,
        current_html: str,
        features: list[dict],
    ) -> str:
        """Регенерировать прототип с учётом предложенных фич."""
        features_text = "\n".join(
            f"- {f['title']}: {f['description']} (голосов: {f.get('votes', 0)})"
            for f in features
        )

        prompt = f"""Улучши существующий HTML прототип, добавив запрошенные фичи.

НАЗВАНИЕ ПРОДУКТА: {idea_title}
ОПИСАНИЕ: {idea_solution}

ТЕКУЩИЙ HTML КОД:
```html
{current_html}
```

ЗАПРОШЕННЫЕ ФИЧИ (отсортированы по популярности):
{features_text}

ЗАДАЧА:
1. Сохрани текущий дизайн и структуру
2. Добавь UI элементы для новых фич
3. Используй Tailwind CSS для стилизации
4. Добавь интерактивность на JavaScript
5. Код должен быть полностью самодостаточным

Ответь ТОЛЬКО HTML кодом, без markdown."""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
        )

        content = response.choices[0].message.content
        # Очищаем от markdown если есть
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        return content

    async def generate_react_prototype(
        self,
        idea_title: str,
        idea_solution: str,
        features: list[str] | None = None,
    ) -> dict:
        """Сгенерировать React прототип с несколькими файлами."""
        features_text = (
            "\n".join(f"- {f}" for f in features) if features else "Базовый функционал"
        )

        prompt = f"""Создай React прототип для стартапа.

НАЗВАНИЕ: {idea_title}
ОПИСАНИЕ: {idea_solution}
ФИЧИ:
{features_text}

Создай структуру проекта с файлами:
1. App.jsx - главный компонент
2. components/Header.jsx - хедер
3. components/Hero.jsx - главная секция
4. components/Features.jsx - секция с фичами
5. styles.css - базовые стили

Используй современный React (функциональные компоненты, хуки).

Ответ в формате JSON:
{{
  "files": [
    {{"path": "App.jsx", "content": "код"}},
    {{"path": "components/Header.jsx", "content": "код"}},
    ...
  ]
}}"""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            response_format={"type": "json_object"},
        )

        try:
            content = response.choices[0].message.content
            return json.loads(content)
        except (json.JSONDecodeError, AttributeError):
            return {"files": []}

    async def modify_code_with_prompt(
        self,
        current_code: str,
        modification_prompt: str,
        language: str = "html",
    ) -> str:
        """Модифицировать код по текстовому запросу от пользователя."""
        prompt = f"""Измени {language.upper()} код согласно запросу пользователя.

ТЕКУЩИЙ КОД:
```{language}
{current_code}
```

ЗАПРОС ПОЛЬЗОВАТЕЛЯ:
{modification_prompt}

ПРАВИЛА:
1. Внеси только запрошенные изменения
2. Сохрани рабочий код
3. Сохрани стиль и форматирование

Ответь ТОЛЬКО кодом, без markdown и объяснений."""

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )

        content = response.choices[0].message.content
        # Очищаем от markdown если есть
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

        return content
