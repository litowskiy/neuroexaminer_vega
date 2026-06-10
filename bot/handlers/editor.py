"""
Редактор вопросов в кабинете преподавателя:
выбор материала → список вопросов с пагинацией → карточка вопроса →
изменение текста / вариантов / правильного варианта / эталона / tf-ответа.
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.handlers.start import get_or_create_user
from bot.keyboards import (
    ANSWER_LETTERS,
    edit_questions_page_keyboard,
    pick_correct_keyboard,
    question_edit_keyboard,
    teacher_edit_docs_keyboard,
)
from bot.states import EditStates
from database.models import AnswerOption, Document, Question

logger = logging.getLogger(__name__)
router = Router()

PAGE_SIZE = 8


async def _load_question(q_id: int, db: AsyncSession) -> Question | None:
    return await db.scalar(
        select(Question).where(Question.id == q_id).options(selectinload(Question.options))
    )


def _question_card_text(question: Question) -> str:
    lines = [f"Вопрос #{question.id}\n", question.text, ""]
    if question.tf_answer is not None:
        lines.append(f"Тип: Верно/Неверно. Правильный ответ: {'Верно' if question.tf_answer else 'Неверно'}")
    else:
        for i, opt in enumerate(question.options[:4]):
            mark = " ← правильный" if opt.is_correct else ""
            lines.append(f"{ANSWER_LETTERS[i]}) {opt.text}{mark}")
        ref = question.reference_answer or "—"
        lines.append(f"\nЭталонный ответ:\n{ref[:800]}")
    return "\n".join(lines)[:4000]


async def _show_question_card(message: Message, question: Question, page: int) -> None:
    await message.edit_text(
        _question_card_text(question),
        reply_markup=question_edit_keyboard(question, question.options, page),
    )


# ---------- Выбор материала ----------

@router.callback_query(F.data == "tedit")
async def edit_docs_list(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    await state.clear()
    user = await get_or_create_user(
        callback.from_user.id, callback.from_user.username, callback.from_user.first_name, db,
    )
    docs = (await db.execute(
        select(Document).where(
            Document.user_id == user.id,
            Document.status == "ready",
        ).order_by(Document.created_at.desc())
    )).scalars().all()

    if not docs:
        await callback.answer("Сначала загрузите материал.", show_alert=True)
        return

    await callback.message.edit_text(
        "Редактирование вопросов.\n\nВыберите материал:",
        reply_markup=teacher_edit_docs_keyboard(docs),
    )


# ---------- Список вопросов ----------

@router.callback_query(F.data.startswith("teditdoc:"))
async def edit_questions_list(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    await state.clear()
    _, doc_id_s, page_s = callback.data.split(":")
    doc_id, page = int(doc_id_s), int(page_s)

    questions = (await db.execute(
        select(Question).where(Question.document_id == doc_id).order_by(Question.id)
    )).scalars().all()
    if not questions:
        await callback.answer("В материале нет вопросов.", show_alert=True)
        return

    total_pages = (len(questions) + PAGE_SIZE - 1) // PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    chunk = questions[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    items = []
    for q in chunk:
        prefix = "[ВН] " if q.tf_answer is not None else ""
        items.append((q.id, f"{prefix}{q.text[:45]}"))

    await callback.message.edit_text(
        f"Вопросы материала (стр. {page + 1}/{total_pages}):",
        reply_markup=edit_questions_page_keyboard(
            doc_id, page, has_prev=page > 0, has_next=page < total_pages - 1, items=items,
        ),
    )


# ---------- Карточка вопроса ----------

@router.callback_query(F.data.startswith("teditq:"))
async def question_card(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    await state.clear()
    _, q_id_s, page_s = callback.data.split(":")
    question = await _load_question(int(q_id_s), db)
    if not question:
        await callback.answer("Вопрос не найден.", show_alert=True)
        return
    await _show_question_card(callback.message, question, int(page_s))


# ---------- Изменение текста вопроса ----------

@router.callback_query(F.data.startswith("teq_text:"))
async def edit_question_text_start(callback: CallbackQuery, state: FSMContext) -> None:
    _, q_id_s, page_s = callback.data.split(":")
    await state.set_state(EditStates.waiting_question_text)
    await state.update_data(edit_q_id=int(q_id_s), edit_page=int(page_s))
    await callback.message.answer(
        "Отправьте новый текст вопроса.\n\nКоманда /menu — отменить."
    )


@router.message(EditStates.waiting_question_text, F.text)
async def edit_question_text_save(message: Message, state: FSMContext, db: AsyncSession) -> None:
    data = await state.get_data()
    question = await _load_question(data["edit_q_id"], db)
    if not question:
        await message.answer("Вопрос не найден.")
        await state.clear()
        return

    question.text = message.text.strip()[:2000]
    await db.commit()
    await state.clear()

    await message.answer(
        "Текст вопроса обновлён.\n\n" + _question_card_text(question),
        reply_markup=question_edit_keyboard(question, question.options, data["edit_page"]),
    )


# ---------- Изменение варианта ответа ----------

@router.callback_query(F.data.startswith("teq_opt:"))
async def edit_option_start(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    _, opt_id_s, q_id_s, page_s = callback.data.split(":")
    opt = await db.scalar(select(AnswerOption).where(AnswerOption.id == int(opt_id_s)))
    if not opt:
        await callback.answer("Вариант не найден.", show_alert=True)
        return

    await state.set_state(EditStates.waiting_option_text)
    await state.update_data(
        edit_opt_id=int(opt_id_s), edit_q_id=int(q_id_s), edit_page=int(page_s),
    )
    await callback.message.answer(
        f"Текущий текст варианта:\n{opt.text}\n\n"
        "Отправьте новый текст.\n\nКоманда /menu — отменить."
    )


@router.message(EditStates.waiting_option_text, F.text)
async def edit_option_save(message: Message, state: FSMContext, db: AsyncSession) -> None:
    data = await state.get_data()
    opt = await db.scalar(select(AnswerOption).where(AnswerOption.id == data["edit_opt_id"]))
    if not opt:
        await message.answer("Вариант не найден.")
        await state.clear()
        return

    opt.text = message.text.strip()[:1000]
    await db.commit()

    question = await _load_question(data["edit_q_id"], db)
    await state.clear()

    await message.answer(
        "Вариант обновлён.\n\n" + _question_card_text(question),
        reply_markup=question_edit_keyboard(question, question.options, data["edit_page"]),
    )


# ---------- Смена правильного варианта ----------

@router.callback_query(F.data.startswith("teq_pick:"))
async def pick_correct_start(callback: CallbackQuery, db: AsyncSession) -> None:
    _, q_id_s, page_s = callback.data.split(":")
    question = await _load_question(int(q_id_s), db)
    if not question or not question.options:
        await callback.answer("Вопрос или варианты не найдены.", show_alert=True)
        return

    await callback.message.edit_text(
        "Выберите правильный вариант:",
        reply_markup=pick_correct_keyboard(question.id, question.options, int(page_s)),
    )


@router.callback_query(F.data.startswith("teq_setcorrect:"))
async def pick_correct_save(callback: CallbackQuery, db: AsyncSession) -> None:
    _, q_id_s, opt_id_s, page_s = callback.data.split(":")
    question = await _load_question(int(q_id_s), db)
    if not question:
        await callback.answer("Вопрос не найден.", show_alert=True)
        return

    for opt in question.options:
        opt.is_correct = (opt.id == int(opt_id_s))
    await db.commit()

    await callback.answer("Правильный вариант обновлён.")
    await _show_question_card(callback.message, question, int(page_s))


# ---------- Изменение эталонного ответа ----------

@router.callback_query(F.data.startswith("teq_ref:"))
async def edit_reference_start(callback: CallbackQuery, state: FSMContext) -> None:
    _, q_id_s, page_s = callback.data.split(":")
    await state.set_state(EditStates.waiting_reference)
    await state.update_data(edit_q_id=int(q_id_s), edit_page=int(page_s))
    await callback.message.answer(
        "Отправьте новый эталонный ответ.\n\nКоманда /menu — отменить."
    )


@router.message(EditStates.waiting_reference, F.text)
async def edit_reference_save(message: Message, state: FSMContext, db: AsyncSession) -> None:
    data = await state.get_data()
    question = await _load_question(data["edit_q_id"], db)
    if not question:
        await message.answer("Вопрос не найден.")
        await state.clear()
        return

    question.reference_answer = message.text.strip()[:4000]
    await db.commit()
    await state.clear()

    await message.answer(
        "Эталонный ответ обновлён.\n\n" + _question_card_text(question),
        reply_markup=question_edit_keyboard(question, question.options, data["edit_page"]),
    )


# ---------- Переключение tf-ответа ----------

@router.callback_query(F.data.startswith("teq_tf:"))
async def toggle_tf(callback: CallbackQuery, db: AsyncSession) -> None:
    _, q_id_s, page_s = callback.data.split(":")
    question = await _load_question(int(q_id_s), db)
    if not question or question.tf_answer is None:
        await callback.answer("Утверждение не найдено.", show_alert=True)
        return

    question.tf_answer = not question.tf_answer
    await db.commit()

    await callback.answer(f"Ответ изменён на: {'Верно' if question.tf_answer else 'Неверно'}")
    await _show_question_card(callback.message, question, int(page_s))
