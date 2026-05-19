"""
Генерирует по 30 обычных вопросов + 30 TF-утверждений для каждой темы
и добавляет их в data/base_questions.json.

Запуск: python scripts/generate_topic_questions.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from openai import AsyncOpenAI

DATA_FILE = Path(__file__).parent.parent / "data" / "base_questions.json"

client = AsyncOpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ.get("OPENAI_BASE_URL"),
)

TOPICS = {
    "git_guide": "Руководство по Git: основы системы контроля версий, команды (init, clone, add, commit, push, pull, fetch, merge, rebase, stash, cherry-pick, reset, revert), ветвление, работа с удалёнными репозиториями, разрешение конфликтов, теги, .gitignore, git flow, pull requests",
    "python_3_12": "Документация по Python 3.12: типы данных, коллекции, функции, ООП, исключения, итераторы и генераторы, декораторы, контекстные менеджеры, async/await, модули стандартной библиотеки, аннотации типов, новые возможности Python 3.12",
    "sql_lecture": "Введение в SQL: SELECT, WHERE, ORDER BY, GROUP BY, HAVING, JOIN (INNER, LEFT, RIGHT, FULL), подзапросы, агрегатные функции, индексы, транзакции, DDL (CREATE, ALTER, DROP), DML (INSERT, UPDATE, DELETE), нормализация, первичные и внешние ключи",
}

COMBINED_PROMPT = """\
Ты эксперт по подготовке к техническим собеседованиям.

Тема: {topic}

Создай ровно {count} вопросов по этой теме.

Каждый вопрос должен содержать ОБА формата:
1. Ровно 4 варианта ответа (один правильный, три правдоподобных неправильных)
2. Развёрнутый эталонный ответ (3–5 предложений)

Вопросы должны быть разнообразными: охватывай разные аспекты темы, не повторяйся.

Ответ — только JSON-массив без пояснений и markdown:
[
  {{
    "text": "Текст вопроса?",
    "options": [
      {{"text": "Правильный вариант", "is_correct": true}},
      {{"text": "Неправильный 1",     "is_correct": false}},
      {{"text": "Неправильный 2",     "is_correct": false}},
      {{"text": "Неправильный 3",     "is_correct": false}}
    ],
    "reference_answer": "Подробный эталонный ответ..."
  }}
]
"""

TF_PROMPT = """\
Ты эксперт по техническим собеседованиям.

Тема: {topic}

Создай ровно {count} утверждений для режима "Верно/Неверно".

Правила:
- Каждое утверждение — конкретный факт или тезис, НЕ вопрос
- Ровно половина истинна, половина — ложна
- Ложные утверждения должны быть реалистичными (типичные заблуждения)
- Утверждение — одно предложение
- Охватывай разные аспекты темы

Ответ — только JSON-массив без пояснений и markdown:
[
  {{"text": "Утверждение", "tf_answer": true}},
  {{"text": "Ложное утверждение", "tf_answer": false}}
]
"""


async def call_api(prompt: str) -> list[dict]:
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=8000,
    )
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)


async def generate_batch(prompt_template: str, topic_desc: str, total: int, batch_size: int = 10) -> list[dict]:
    results = []
    batches = (total + batch_size - 1) // batch_size
    for i in range(batches):
        count = min(batch_size, total - len(results))
        batch = await call_api(prompt_template.format(topic=topic_desc, count=count))
        results.extend(batch)
        print(f"  батч {i+1}/{batches}: +{len(batch)} (итого {len(results)})")
    return results


async def generate_for_topic(category: str, topic_desc: str) -> list[dict]:
    print(f"\n[{category}] Генерирую обычные вопросы...")
    combined = await generate_batch(COMBINED_PROMPT, topic_desc, total=30)
    for q in combined:
        q["is_open"] = False
    print(f"  → итого {len(combined)} вопросов с вариантами")

    print(f"[{category}] Генерирую TF-утверждения...")
    tf = await generate_batch(TF_PROMPT, topic_desc, total=30)
    print(f"  → итого {len(tf)} утверждений True/False")

    return combined + tf


async def main() -> None:
    with open(DATA_FILE, encoding="utf-8") as f:
        data: dict[str, list[dict]] = json.load(f)

    for category, topic_desc in TOPICS.items():
        existing = data.get(category, [])
        existing_combined = [q for q in existing if "tf_answer" not in q]
        existing_tf = [q for q in existing if "tf_answer" in q]

        need_combined = max(0, 30 - len(existing_combined))
        need_tf = max(0, 30 - len(existing_tf))

        if need_combined == 0 and need_tf == 0:
            print(f"[{category}] полный комплект, пропускаю.")
            continue

        added = []
        if need_combined:
            print(f"\n[{category}] не хватает {need_combined} обычных вопросов, генерирую...")
            batch = await generate_batch(COMBINED_PROMPT, topic_desc, total=need_combined)
            for q in batch:
                q["is_open"] = False
            added.extend(batch)

        if need_tf:
            print(f"[{category}] не хватает {need_tf} TF-утверждений, генерирую...")
            batch = await generate_batch(TF_PROMPT, topic_desc, total=need_tf)
            added.extend(batch)

        data[category] = existing + added
        print(f"[{category}] добавлено {len(added)} вопросов.")

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("\nГотово. base_questions.json обновлён.")


if __name__ == "__main__":
    asyncio.run(main())
