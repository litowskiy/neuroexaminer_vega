import json
import logging
from openai import AsyncOpenAI
from config import settings

logger = logging.getLogger(__name__)
client = AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY,
    base_url=settings.OPENAI_BASE_URL,
)

MAX_TEXT_CHARS = 12_000

_COMBINED_PROMPT = """\
Ты эксперт по подготовке к техническим собеседованиям.

На основе учебного материала ниже создай {count} вопросов.

Каждый вопрос должен содержать ОБА формата одновременно:
1. Ровно 4 варианта ответа для формата "тест" (один правильный, три правдоподобных неправильных)
2. Развёрнутый эталонный ответ (3–5 предложений) для формата "открытый вопрос"

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
    "reference_answer": "Подробный эталонный ответ на этот вопрос..."
  }}
]

Учебный материал:
{text}
"""


_TF_PROMPT = """\
Ты эксперт по техническим собеседованиям.

По учебному материалу ниже создай {count} утверждений для режима "Верно/Неверно".

Правила:
- Каждое утверждение — конкретный факт или тезис, НЕ вопрос
- Примерно половина утверждений истинна, половина — ложна
- Ложные утверждения должны быть реалистичными (типичные заблуждения)
- Утверждение должно быть коротким: одно предложение

Ответ — только JSON-массив без пояснений и markdown:
[
  {{"text": "Утверждение о теме", "tf_answer": true}},
  {{"text": "Ложное утверждение о теме", "tf_answer": false}}
]

Учебный материал:
{text}
"""


async def generate_tf_statements(text: str, count: int = 10) -> list[dict]:
    """Генерирует утверждения True/False для режима 'Верно/Неверно'."""
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n...[текст обрезан]"
    return await _call_api(_TF_PROMPT.format(text=text, count=count))


async def generate_questions_from_text(text: str, count: int = 20) -> list[dict]:
    """
    Генерирует вопросы по тексту. Каждый вопрос содержит и варианты ответов,
    и эталонный ответ — один набор вопросов работает в любом режиме тренировки.
    """
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n...[текст обрезан]"

    raw = await _call_api(_COMBINED_PROMPT.format(text=text, count=count))
    return raw


async def evaluate_open_answer(question: str, reference: str, user_answer: str) -> bool:
    """
    Проверяет текстовый ответ пользователя по эталону через OpenAI.
    Возвращает True если ответ правильный или в основном правильный.
    """
    prompt = (
        f"Вопрос: {question}\n"
        f"Эталонный ответ: {reference}\n"
        f"Ответ студента: {user_answer}\n\n"
        "Ответ студента правильный или в основном правильный? "
        "Ответь ТОЛЬКО одним словом: YES или NO."
    )
    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=5,
    )
    result = response.choices[0].message.content.strip().upper()
    return result.startswith("YES")


async def _call_api(prompt: str) -> list[dict]:
    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=4096,
    )
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)
