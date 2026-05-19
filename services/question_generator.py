import json
import logging
import math

from openai import AsyncOpenAI

from config import settings

logger = logging.getLogger(__name__)
client = AsyncOpenAI(
    api_key=settings.OPENAI_API_KEY,
    base_url=settings.OPENAI_BASE_URL,
)

MAX_TEXT_CHARS = 12_000
EMBEDDING_MODEL = "text-embedding-3-small"
COSINE_CORRECT_THRESHOLD = 0.82
COSINE_WRONG_THRESHOLD = 0.35

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
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n...[текст обрезан]"
    return await _call_api(_TF_PROMPT.format(text=text, count=count))


async def generate_questions_from_text(text: str, count: int = 20) -> list[dict]:
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n...[текст обрезан]"
    return await _call_api(_COMBINED_PROMPT.format(text=text, count=count))


async def evaluate_open_answer(question: str, reference: str, user_answer: str) -> bool:
    """
    Оценка открытого ответа:
    1. Косинусное сходство через OpenAI embeddings (быстро, без GPT)
    2. Серая зона → GPT YES/NO
    Если API недоступен — бросает исключение, caller показывает самооценку.
    """
    ref_emb, ans_emb = await _get_embeddings([reference, user_answer])
    similarity = _cosine(ref_emb, ans_emb)
    logger.debug("Cosine similarity: %.3f", similarity)

    if similarity >= COSINE_CORRECT_THRESHOLD:
        return True
    if similarity <= COSINE_WRONG_THRESHOLD:
        return False

    return await _gpt_evaluate(question, reference, user_answer)


async def _get_embeddings(texts: list[str]) -> list[list[float]]:
    resp = await client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
    resp.data.sort(key=lambda e: e.index)
    return [e.embedding for e in resp.data]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


async def _gpt_evaluate(question: str, reference: str, user_answer: str) -> bool:
    prompt = (
        f"Вопрос: {question}\n"
        f"Эталонный ответ: {reference}\n"
        f"Ответ студента: {user_answer}\n\n"
        "Оцени ответ студента.\n\n"
        "Засчитывай как правильный (YES) если:\n"
        "- студент верно понял суть, даже если формулировка неформальная\n"
        "- ответ неполный, но не содержит ошибок\n"
        "- использованы синонимы или упрощённые объяснения\n\n"
        "Засчитывай как неправильный (NO) если:\n"
        "- студент перепутал понятия\n"
        "- ответ содержит фактическую ошибку\n"
        "- студент написал 'не знаю' или не ответил по существу\n\n"
        "Ответь ТОЛЬКО одним словом: YES или NO."
    )
    response = await client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=5,
    )
    return response.choices[0].message.content.strip().upper().startswith("YES")


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
