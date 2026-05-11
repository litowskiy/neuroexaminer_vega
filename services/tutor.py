"""
AI-тьютор: LLM-агент с структурированным выводом (LangChain with_structured_output).

Агент получает список вопросов из материала и историю диалога,
затем сам решает что делать: задать вопрос, оценить ответ, объяснить тему.
"""
import asyncio
import logging
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from config import settings

logger = logging.getLogger(__name__)


class TutorAction(BaseModel):
    action: Literal["ask_question", "evaluate", "explain", "encourage", "summarize"]
    message: str = Field(description="Текст ответа тьютора пользователю")
    question_id: int | None = Field(
        default=None,
        description="ID вопроса из списка — заполняется только при action=ask_question",
    )
    is_correct: bool | None = Field(
        default=None,
        description="Правильно ли ответил студент — заполняется только при action=evaluate",
    )


_SYSTEM_TEMPLATE = """\
Ты — AI-тьютор, который помогает студенту изучить учебный материал.

Доступные действия:
- ask_question: задать студенту вопрос из банка (укажи question_id)
- evaluate: оценить ответ студента на заданный вопрос (укажи is_correct)
- explain: объяснить тему или понятие (без вопроса)
- encourage: мотивировать студента, кратко похвалить прогресс
- summarize: подвести итог сессии

Правила работы:
1. Начинай сессию с action=ask_question.
2. После ответа студента всегда используй action=evaluate:
   - Дай развёрнутую обратную связь (2-4 предложения), объясни почему правильно или нет.
   - Если неправильно — объясни верный ответ.
3. После каждой оценки предложи следующий шаг (кнопка «Следующий вопрос» появится автоматически).
4. Если студент задаёт вопрос вместо ответа — используй action=explain.
5. Выбирай вопросы, которые ещё не задавались (список задаваемых в истории).
6. Старайся чередовать темы, не задавай два похожих вопроса подряд.

Прогресс студента: {correct}/{asked} правильных ответов.
{rag_section}
Банк вопросов (id: текст):
{questions_list}
"""

_RAG_SECTION = """\
Контекст из учебного материала (используй для объяснений):
---
{context}
---
"""


def _make_llm() -> ChatOpenAI:
    kwargs: dict = dict(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0.5,
    )
    if settings.OPENAI_BASE_URL:
        kwargs["base_url"] = settings.OPENAI_BASE_URL
    return ChatOpenAI(**kwargs)


def _build_messages(
    system_prompt: str,
    history: list[tuple[str, str]],
    user_message: str | None,
) -> list:
    msgs = [SystemMessage(content=system_prompt)]
    window = history[-(settings.CHAT_HISTORY_WINDOW):]
    for human, ai in window:
        msgs.append(HumanMessage(content=human))
        msgs.append(AIMessage(content=ai))
    if user_message is not None:
        msgs.append(HumanMessage(content=user_message))
    else:
        msgs.append(HumanMessage(content="[система] Продолжай: задай следующий вопрос из банка, который ещё не задавался."))
    return msgs


def _run_tutor_sync(
    user_message: str | None,
    questions: list[dict],
    history: list[tuple[str, str]],
    current_question_id: int | None,
    correct_count: int,
    asked_count: int,
    rag_context: str | None,
) -> TutorAction:
    questions_list = "\n".join(
        f"  {q['id']}: {q['text']}"
        for q in questions
    )
    rag_section = (
        _RAG_SECTION.format(context=rag_context[:2000])
        if rag_context else ""
    )
    system_prompt = _SYSTEM_TEMPLATE.format(
        correct=correct_count,
        asked=asked_count,
        rag_section=rag_section,
        questions_list=questions_list,
    )
    if current_question_id is not None:
        q = next((q for q in questions if q["id"] == current_question_id), None)
        if q and user_message:
            context_note = (
                f"\n[Студент отвечал на вопрос #{current_question_id}: «{q['text']}»"
                f" Эталонный ответ: {q.get('reference_answer', 'нет')}]"
            )
            user_message = user_message + context_note

    llm = _make_llm()
    structured = llm.with_structured_output(TutorAction)
    msgs = _build_messages(system_prompt, history, user_message)
    return structured.invoke(msgs)


async def tutor_step(
    user_message: str | None,
    questions: list[dict],
    history: list[tuple[str, str]],
    current_question_id: int | None,
    correct_count: int,
    asked_count: int,
    rag_context: str | None = None,
) -> TutorAction:
    """
    Один шаг тьютора. user_message=None — начало сессии.
    questions — список dict с ключами: id, text, reference_answer.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        _run_tutor_sync,
        user_message,
        questions,
        history,
        current_question_id,
        correct_count,
        asked_count,
        rag_context,
    )
