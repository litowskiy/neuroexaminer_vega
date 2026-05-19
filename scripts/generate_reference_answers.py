"""
Одноразовый скрипт: генерирует reference_answer для вопросов в base_questions.json,
у которых его нет (кроме tf-утверждений).

Запуск: python scripts/generate_reference_answers.py
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


async def generate_reference(question_text: str, correct_answer: str | None) -> str:
    hint = f"\nПравильный вариант ответа: {correct_answer}" if correct_answer else ""
    prompt = (
        f"Вопрос: {question_text}{hint}\n\n"
        "Напиши развёрнутый эталонный ответ на этот вопрос (3–5 предложений). "
        "Только текст ответа, без вводных фраз."
    )
    resp = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=300,
    )
    return resp.choices[0].message.content.strip()


async def main() -> None:
    with open(DATA_FILE, encoding="utf-8") as f:
        data: dict[str, list[dict]] = json.load(f)

    total = 0
    for category, questions in data.items():
        for q in questions:
            if q.get("tf_answer") is not None:
                continue
            if q.get("reference_answer"):
                continue

            correct_opt = next(
                (o["text"] for o in q.get("options", []) if o.get("is_correct")), None
            )
            print(f"[{category}] {q['text'][:70]}...")
            q["reference_answer"] = await generate_reference(q["text"], correct_opt)
            print(f"  → {q['reference_answer'][:80]}...")
            total += 1

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"\nГотово. Обновлено вопросов: {total}")


if __name__ == "__main__":
    asyncio.run(main())
