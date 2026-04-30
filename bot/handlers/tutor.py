"""
AI-тьютор: интерактивное обучение с LLM-агентом.

Тьютор сам решает что делать: задаёт вопросы, оценивает ответы с развёрнутой
обратной связью, объясняет темы. Работает как с документами (+ RAG),
так и со встроенными темами.
"""
import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import (
    BUILT_IN_TOPICS,
    main_menu_keyboard,
    stop_tutor_keyboard,
    tutor_next_keyboard,
)
from bot.states import TutorStates
from database.models import Document, Question
from services.tutor import tutor_step
from services.vector_store import (
    build_index,
    index_exists,
    load_document_text,
    retrieve_chunks,
)

logger = logging.getLogger(__name__)
router = Router()


def _questions_to_dicts(questions: list[Question]) -> list[dict]:
    return [
        {
            "id": q.id,
            "text": q.text,
            "reference_answer": q.reference_answer or "",
        }
        for q in questions
        if q.tf_answer is None
    ]


async def _ensure_index(text_hash: str, status_msg: Message) -> bool:
    """Строит FAISS-индекс если его нет. Возвращает False при ошибке."""
    if index_exists(text_hash):
        return True
    text = load_document_text(text_hash)
    if not text:
        await status_msg.edit_text(
            "Текст документа не найден. Загрузите файл заново.",
            reply_markup=main_menu_keyboard(),
        )
        return False
    try:
        await build_index(text, text_hash)
    except Exception as exc:
        logger.error("Index build failed: %s", exc)
        await status_msg.edit_text(
            f"Не удалось создать индекс: {exc}",
            reply_markup=main_menu_keyboard(),
        )
        return False
    return True


async def _run_step_and_reply(
    message: Message,
    state: FSMContext,
    user_message: str | None,
) -> None:
    data = await state.get_data()
    questions: list[dict] = data.get("tutor_questions", [])
    history: list = data.get("tutor_history", [])
    current_q_id: int | None = data.get("tutor_current_q_id")
    text_hash: str | None = data.get("tutor_text_hash")
    correct: int = data.get("tutor_correct", 0)
    asked: int = data.get("tutor_asked", 0)

    rag_context: str | None = None
    if text_hash and user_message:
        try:
            chunks = await retrieve_chunks(text_hash, user_message, k=3)
            rag_context = "\n\n".join(chunks)
        except Exception as exc:
            logger.warning("RAG retrieve failed (non-fatal): %s", exc)

    thinking_msg = await message.answer("Тьютор думает...")

    try:
        action = await tutor_step(
            user_message=user_message,
            questions=questions,
            history=history,
            current_question_id=current_q_id,
            correct_count=correct,
            asked_count=asked,
            rag_context=rag_context,
        )
    except Exception as exc:
        logger.error("Tutor step failed: %s", exc)
        await thinking_msg.edit_text(
            f"Ошибка тьютора: {exc}",
            reply_markup=stop_tutor_keyboard(),
        )
        return

    new_current_q_id: int | None = None
    new_correct = correct
    new_asked = asked
    keyboard = stop_tutor_keyboard()

    if action.action == "ask_question":
        new_current_q_id = action.question_id
        new_asked = asked + 1
        keyboard = stop_tutor_keyboard()

    elif action.action == "evaluate":
        new_current_q_id = None
        if action.is_correct:
            new_correct = correct + 1
        keyboard = tutor_next_keyboard()

    elif action.action in ("explain", "encourage"):
        new_current_q_id = current_q_id
        keyboard = stop_tutor_keyboard()

    elif action.action == "summarize":
        new_current_q_id = None
        keyboard = tutor_next_keyboard()

    history_entry = (user_message or "", action.message)
    new_history = history + [history_entry]

    await state.update_data(
        tutor_history=new_history,
        tutor_current_q_id=new_current_q_id,
        tutor_correct=new_correct,
        tutor_asked=new_asked,
    )

    await thinking_msg.edit_text(html.escape(action.message), reply_markup=keyboard)


async def _start_tutor(
    message: Message,
    state: FSMContext,
    questions: list[Question],
    label: str,
    text_hash: str | None = None,
) -> None:
    q_dicts = _questions_to_dicts(questions)
    if not q_dicts:
        await message.edit_text(
            "По этому материалу нет подходящих вопросов для тьютора.",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.set_state(TutorStates.in_session)
    await state.update_data(
        tutor_label=label,
        tutor_text_hash=text_hash,
        tutor_questions=q_dicts,
        tutor_history=[],
        tutor_current_q_id=None,
        tutor_correct=0,
        tutor_asked=0,
    )

    await message.edit_text(
        f"AI-тьютор: {label}\n\n"
        "Тьютор будет задавать вопросы и давать развёрнутую обратную связь.\n"
        "Отвечайте текстом.\n\n"
        "Нажмите «Завершить» в любой момент.",
        reply_markup=stop_tutor_keyboard(),
    )

    await _run_step_and_reply(message, state, user_message=None)


@router.callback_query(F.data.startswith("mat_tutor:"))
async def start_tutor_from_material(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    doc_id = int(callback.data.split(":", 1)[1])
    doc = await db.scalar(select(Document).where(Document.id == doc_id))
    if not doc or doc.status != "ready":
        await callback.answer("Материал недоступен.", show_alert=True)
        return

    questions = (await db.execute(
        select(Question).where(Question.document_id == doc_id)
    )).scalars().all()

    status_msg = await callback.message.edit_text(
        "Подготавливаю AI-тьютора..."
    )
    ok = await _ensure_index(doc.text_hash, status_msg)
    if not ok:
        return

    await _start_tutor(
        message=status_msg,
        state=state,
        questions=list(questions),
        label=doc.filename,
        text_hash=doc.text_hash,
    )


@router.callback_query(F.data.startswith("topic_tutor:"))
async def start_tutor_from_topic(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    topic_key = callback.data.split(":", 1)[1]
    label = BUILT_IN_TOPICS.get(topic_key, topic_key)

    questions = (await db.execute(
        select(Question).where(
            Question.category == topic_key,
            Question.document_id.is_(None),
        )
    )).scalars().all()

    if not questions:
        await callback.answer(f"По теме «{label}» нет вопросов.", show_alert=True)
        return

    await _start_tutor(
        message=callback.message,
        state=state,
        questions=list(questions),
        label=label,
        text_hash=None,
    )


@router.message(TutorStates.in_session, F.text)
async def handle_tutor_message(message: Message, state: FSMContext) -> None:
    await _run_step_and_reply(message, state, user_message=message.text)


@router.callback_query(TutorStates.in_session, F.data == "tutor_next")
async def tutor_next_question(callback: CallbackQuery, state: FSMContext) -> None:
    await _run_step_and_reply(callback.message, state, user_message=None)


@router.callback_query(TutorStates.in_session, F.data == "tutor_stop")
async def stop_tutor(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    correct: int = data.get("tutor_correct", 0)
    asked: int = data.get("tutor_asked", 0)
    label: str = data.get("tutor_label", "")

    pct = round(correct / asked * 100) if asked else 0
    await state.clear()
    await callback.message.edit_text(
        f"Сессия с тьютором завершена.\n\n"
        f"Тема: {label}\n"
        f"Вопросов задано: {asked}\n"
        f"Правильных ответов: {correct} ({pct}%)\n\n"
        "Главное меню:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(TutorStates.in_session)
async def handle_non_text_in_tutor(message: Message) -> None:
    await message.answer(
        "Отвечайте текстом.",
        reply_markup=stop_tutor_keyboard(),
    )
