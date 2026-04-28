"""
Логика выбора режима и проведения тренировочной сессии.

Режимы:
  closed — с вариантами ответа (2/3/4 на выбор)
  open — пишешь ответ текстом, бот проверяет через OpenAI
  self_eval — утверждения True/False: "Верно" / "Неверно"
  mixed — комбинация closed+open+flashcard для обычных вопросов
  marathon — все обычные вопросы, формат по умолчанию

Фильтрация:
  self_eval → только tf-утверждения (tf_answer IS NOT NULL)
  все остальные режимы → только обычные вопросы (tf_answer IS NULL)
"""
import logging
import random

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.keyboards import (
    ANSWER_LETTERS,
    answer_options_keyboard,
    cancel_quiz_keyboard,
    count_selection_keyboard,
    know_dontknow_keyboard,
    main_menu_keyboard,
    mixed_formats_keyboard,
    mode_selection_keyboard,
    next_question_keyboard,
    options_count_keyboard,
    self_eval_keyboard,
    tf_keyboard,
)
from bot.states import QuizSetupStates, QuizStates
from database.models import AnswerOption, Question, TrainingSession, User
from services.question_generator import evaluate_open_answer

logger = logging.getLogger(__name__)
router = Router()

async def start_quiz_setup(
    message: Message,
    user: User,
    all_questions: list[Question],
    db: AsyncSession,
    state: FSMContext,
    topic_label: str,
) -> None:
    if not all_questions:
        await message.edit_text("Нет доступных вопросов.", reply_markup=main_menu_keyboard())
        return

    await state.set_state(QuizSetupStates.selecting_mode)
    await state.update_data(
        all_question_ids=[q.id for q in all_questions],
        topic_label=topic_label,
    )
    regular = [q for q in all_questions if q.tf_answer is None]
    tf = [q for q in all_questions if q.tf_answer is not None]
    await message.edit_text(
        f"Тема: {topic_label}\n"
        f"Обычных вопросов: {len(regular)}\n"
        f"Утверждений (Верно/Неверно): {len(tf)}\n\n"
        "Выберите режим тренировки:",
        reply_markup=mode_selection_keyboard(),
    )

@router.callback_query(QuizSetupStates.selecting_mode, F.data.startswith("mode:"))
async def handle_mode_selection(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    mode = callback.data.split(":", 1)[1]

    data = await state.get_data()
    all_ids: list[int] = data["all_question_ids"]
    topic_label: str = data["topic_label"]
    questions = await _load_questions_by_ids(all_ids, db)

    if mode == "mixed":
        await state.set_state(QuizSetupStates.selecting_mixed_formats)
        await state.update_data(selected_formats=["closed"])
        await callback.message.edit_text(
            f"Тема: {topic_label}\n\n"
            "Выберите форматы для смешанного режима\n"
            "(можно выбрать несколько):",
            reply_markup=mixed_formats_keyboard(["closed"]),
        )
        return

    filtered = _filter_questions(questions, mode)

    if not filtered:
        await callback.answer(
            "Для этого режима нет вопросов по данной теме.", show_alert=True
        )
        return

    if mode == "marathon":
        user = await _get_user(callback.from_user.id, db)
        await _create_and_start_session(
            callback.message, user, filtered, db, state, topic_label, mode,
            marathon=True, options_count=4,
        )
        return

    if mode == "closed":
        await state.update_data(filtered_question_ids=[q.id for q in filtered], mode=mode)
        await state.set_state(QuizSetupStates.selecting_options_count)
        await callback.message.edit_text(
            f"Тема: {topic_label}\n\n"
            "Сколько вариантов ответа показывать?",
            reply_markup=options_count_keyboard(),
        )
        return

    await state.update_data(filtered_question_ids=[q.id for q in filtered], mode=mode, options_count=4)
    await state.set_state(QuizSetupStates.selecting_count)
    await callback.message.edit_text(
        f"Тема: {topic_label}\n"
        f"Вопросов доступно: {len(filtered)}\n\n"
        f"Выберите количество или введите число от 1 до {len(filtered)}:",
        reply_markup=count_selection_keyboard(len(filtered)),
    )

@router.callback_query(QuizSetupStates.selecting_options_count, F.data.startswith("opts:"))
async def handle_options_count(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    opts_count = int(callback.data.split(":")[1])
    await state.update_data(options_count=opts_count)

    data = await state.get_data()
    ids = data["filtered_question_ids"]
    topic_label = data["topic_label"]

    await state.set_state(QuizSetupStates.selecting_count)
    await callback.message.edit_text(
        f"Тема: {topic_label}\n"
        f"Вопросов доступно: {len(ids)}\n"
        f"Вариантов ответа: {opts_count}\n\n"
        f"Выберите количество или введите число от 1 до {len(ids)}:",
        reply_markup=count_selection_keyboard(len(ids)),
    )


@router.callback_query(QuizSetupStates.selecting_options_count, F.data == "setup_back_to_mode")
async def back_from_opts_to_mode(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    data = await state.get_data()
    questions = await _load_questions_by_ids(data["all_question_ids"], db)
    regular = [q for q in questions if q.tf_answer is None]
    tf = [q for q in questions if q.tf_answer is not None]
    await state.set_state(QuizSetupStates.selecting_mode)
    await callback.message.edit_text(
        f"Тема: {data['topic_label']}\n"
        f"Обычных вопросов: {len(regular)}\n"
        f"Утверждений (Верно/Неверно): {len(tf)}\n\n"
        "Выберите режим тренировки:",
        reply_markup=mode_selection_keyboard(),
    )

@router.callback_query(QuizSetupStates.selecting_mixed_formats, F.data.startswith("mix_toggle:"))
async def toggle_mixed_format(
    callback: CallbackQuery, state: FSMContext
) -> None:
    fmt = callback.data.split(":", 1)[1]
    data = await state.get_data()
    selected: list[str] = list(data.get("selected_formats", ["closed"]))

    if fmt in selected:
        if len(selected) > 1:
            selected.remove(fmt)
        else:
            await callback.answer("Должен быть выбран хотя бы один формат.")
            return
    else:
        selected.append(fmt)

    await state.update_data(selected_formats=selected)
    await callback.message.edit_reply_markup(reply_markup=mixed_formats_keyboard(selected))


@router.callback_query(QuizSetupStates.selecting_mixed_formats, F.data == "mix_confirm")
async def confirm_mixed_formats(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    data = await state.get_data()
    selected: list[str] = data.get("selected_formats", ["closed"])
    topic_label: str = data["topic_label"]
    all_ids: list[int] = data["all_question_ids"]

    questions = await _load_questions_by_ids(all_ids, db)
    mode = ",".join(sorted(selected))
    filtered = _filter_questions(questions, mode)

    if not filtered:
        await callback.answer("Нет вопросов для выбранных форматов.", show_alert=True)
        return

    opts_count = 4
    if "closed" in selected:
        await state.update_data(filtered_question_ids=[q.id for q in filtered], mode=mode)
        await state.set_state(QuizSetupStates.selecting_options_count)
        await callback.message.edit_text(
            f"Тема: {topic_label}\n\nСколько вариантов ответа показывать для закрытых вопросов?",
            reply_markup=options_count_keyboard(),
        )
        return

    await state.update_data(filtered_question_ids=[q.id for q in filtered], mode=mode, options_count=opts_count)
    await state.set_state(QuizSetupStates.selecting_count)
    await callback.message.edit_text(
        f"Тема: {topic_label}\n"
        f"Вопросов доступно: {len(filtered)}\n\n"
        f"Выберите количество или введите число от 1 до {len(filtered)}:",
        reply_markup=count_selection_keyboard(len(filtered)),
    )


@router.callback_query(QuizSetupStates.selecting_mixed_formats, F.data == "mix_back")
async def mix_back(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    data = await state.get_data()
    questions = await _load_questions_by_ids(data["all_question_ids"], db)
    regular = [q for q in questions if q.tf_answer is None]
    tf = [q for q in questions if q.tf_answer is not None]
    await state.set_state(QuizSetupStates.selecting_mode)
    await callback.message.edit_text(
        f"Тема: {data['topic_label']}\n"
        f"Обычных вопросов: {len(regular)}\n"
        f"Утверждений (Верно/Неверно): {len(tf)}\n\n"
        "Выберите режим тренировки:",
        reply_markup=mode_selection_keyboard(),
    )

@router.callback_query(QuizSetupStates.selecting_count, F.data.startswith("count:"))
async def handle_count_selection(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    count = int(callback.data.split(":", 1)[1])
    data = await state.get_data()
    questions = await _load_questions_by_ids(data["filtered_question_ids"], db)
    random.shuffle(questions)

    user = await _get_user(callback.from_user.id, db)
    await _create_and_start_session(
        callback.message, user, questions[:count], db, state,
        data["topic_label"], data.get("mode", "mixed"),
        marathon=False, options_count=data.get("options_count", 4),
    )


@router.message(QuizSetupStates.selecting_count)
async def handle_custom_count_input(
    message: Message, state: FSMContext, db: AsyncSession
) -> None:
    text = message.text.strip() if message.text else ""
    if not text.isdigit() or int(text) < 1:
        await message.answer("Введите целое число больше 0.")
        return

    data = await state.get_data()
    ids = data["filtered_question_ids"]
    count = min(int(text), len(ids))

    questions = await _load_questions_by_ids(ids, db)
    random.shuffle(questions)

    user = await _get_user(message.from_user.id, db)
    await _create_and_start_session(
        message, user, questions[:count], db, state,
        data["topic_label"], data.get("mode", "mixed"),
        marathon=False, options_count=data.get("options_count", 4),
        use_answer=True,
    )


@router.callback_query(QuizSetupStates.selecting_count, F.data == "setup_back_to_mode")
async def back_to_mode(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    data = await state.get_data()
    questions = await _load_questions_by_ids(data["all_question_ids"], db)
    regular = [q for q in questions if q.tf_answer is None]
    tf = [q for q in questions if q.tf_answer is not None]
    await state.set_state(QuizSetupStates.selecting_mode)
    await callback.message.edit_text(
        f"Тема: {data['topic_label']}\n"
        f"Обычных вопросов: {len(regular)}\n"
        f"Утверждений (Верно/Неверно): {len(tf)}\n\n"
        "Выберите режим тренировки:",
        reply_markup=mode_selection_keyboard(),
    )

async def _create_and_start_session(
    message: Message,
    user: User,
    questions: list[Question],
    db: AsyncSession,
    state: FSMContext,
    topic_label: str,
    mode: str,
    marathon: bool,
    options_count: int = 4,
    use_answer: bool = False,
) -> None:
    random.shuffle(questions)
    session = TrainingSession(user_id=user.id, question_ids="[]", mode=mode)
    session.set_question_ids([q.id for q in questions])
    db.add(session)
    await db.commit()
    await db.refresh(session)

    await state.set_state(QuizStates.in_session)
    await state.update_data(
        session_id=session.id,
        current_open_question_id=None,
        options_count=options_count,
    )

    count_line = "Марафон" if marathon else f"Вопросов: {len(questions)}"
    intro = f"Тема: {topic_label}\n{count_line}\n\nНачинаем."

    if use_answer:
        msg = await message.answer(intro)
    else:
        await message.edit_text(intro)
        msg = message

    await _show_next_question(msg, session, db, state)


async def _show_next_question(
    message: Message,
    session: TrainingSession,
    db: AsyncSession,
    state: FSMContext,
) -> None:
    ids = session.get_question_ids()
    if session.current_index >= len(ids):
        await _finish_session(message, session, db, state)
        return

    q_id = ids[session.current_index]
    question = await db.scalar(
        select(Question).where(Question.id == q_id).options(selectinload(Question.options))
    )
    if not question:
        session.current_index += 1
        await db.commit()
        await _show_next_question(message, session, db, state)
        return

    fmt = _pick_format(question, session.mode)
    progress = f"[{session.current_index + 1}/{session.total_count}]"

    if fmt == "self_eval":
        if question.tf_answer is not None:
            await message.answer(
                f"Вопрос {progress}\n\nУтверждение:\n\n{question.text}",
                reply_markup=tf_keyboard(question.id),
            )
        else:
            await message.answer(
                f"Вопрос {progress}\n\n{question.text}",
                reply_markup=know_dontknow_keyboard(question.id),
            )
        await state.update_data(current_open_question_id=None)

    elif fmt == "open":
        await state.update_data(current_open_question_id=question.id)
        await message.answer(
            f"Вопрос {progress}\n\n{question.text}\n\nНапишите ответ:",
            reply_markup=cancel_quiz_keyboard(),
        )

    else:
        await state.update_data(current_open_question_id=None)
        data = await state.get_data()
        opts_count = data.get("options_count", 4)

        opts = (await db.execute(
            select(AnswerOption)
            .where(AnswerOption.question_id == question.id)
            .order_by(AnswerOption.order)
        )).scalars().all()

        show_opts = opts[:opts_count]
        opts_text = "\n".join(
            f"{ANSWER_LETTERS[i]}) {opt.text}" for i, opt in enumerate(show_opts)
        )
        await message.answer(
            f"Вопрос {progress}\n\n{question.text}\n\n{opts_text}",
            reply_markup=answer_options_keyboard(show_opts, question.id),
        )

@router.callback_query(QuizStates.in_session, F.data.startswith("answer:"))
async def handle_mcq_answer(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    _, q_id_s, opt_id_s = callback.data.split(":")
    q_id, selected_opt_id = int(q_id_s), int(opt_id_s)

    opts = (await db.execute(
        select(AnswerOption).where(AnswerOption.question_id == q_id).order_by(AnswerOption.order)
    )).scalars().all()

    correct_opt = next((o for o in opts if o.is_correct), None)
    selected_opt = next((o for o in opts if o.id == selected_opt_id), None)
    is_correct = bool(selected_opt and selected_opt.is_correct)

    session = await _load_session(state, db)
    if session:
        if is_correct:
            session.correct_count += 1
        session.current_index += 1
        await db.commit()

    result = "Правильно." if is_correct else f"Неверно. Правильный ответ: {correct_opt.text if correct_opt else '?'}"
    await callback.message.edit_text(f"{callback.message.text}\n\n{result}")

    if session:
        await _show_next_question(callback.message, session, db, state)

@router.message(QuizStates.in_session)
async def handle_open_answer(
    message: Message, state: FSMContext, db: AsyncSession
) -> None:
    data = await state.get_data()
    q_id = data.get("current_open_question_id")
    if not q_id:
        await message.answer("Используйте кнопки для ответа.", reply_markup=cancel_quiz_keyboard())
        return

    question = await db.scalar(select(Question).where(Question.id == q_id))
    await state.update_data(current_open_question_id=None)

    if not question or not question.reference_answer:
        await message.answer("Ошибка: эталонный ответ не найден.", reply_markup=cancel_quiz_keyboard())
        return

    checking_msg = await message.answer("Проверяю ответ...")

    try:
        is_correct = await evaluate_open_answer(
            question.text, question.reference_answer, message.text
        )
    except Exception as exc:
        logger.error("Answer evaluation failed: %s", exc)
        await checking_msg.edit_text(
            f"Эталонный ответ:\n\n{question.reference_answer}\n\nОцените свой ответ:",
            reply_markup=self_eval_keyboard(q_id),
        )
        return

    session = await _load_session(state, db)
    if session:
        if is_correct:
            session.correct_count += 1
        session.current_index += 1
        await db.commit()

    verdict = "Правильно." if is_correct else "Неточно."
    await checking_msg.edit_text(f"{verdict}\n\nЭталонный ответ:\n\n{question.reference_answer}")

    if session:
        await _show_next_question(checking_msg, session, db, state)

@router.callback_query(QuizStates.in_session, F.data.startswith("self_eval:"))
async def handle_self_eval(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    is_correct = callback.data.split(":")[2] == "correct"
    session = await _load_session(state, db)
    if session:
        if is_correct:
            session.correct_count += 1
        session.current_index += 1
        await db.commit()

    verdict = "Отмечено как правильно." if is_correct else "Отмечено как неправильно."
    await callback.message.edit_text(f"{callback.message.text}\n\n{verdict}")

    if session:
        await _show_next_question(callback.message, session, db, state)

@router.callback_query(QuizStates.in_session, F.data.startswith("tf:"))
async def handle_tf_answer(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    _, q_id_s, user_answer = callback.data.split(":")
    q_id = int(q_id_s)
    user_says_true = user_answer == "true"

    question = await db.scalar(select(Question).where(Question.id == q_id))
    if not question or question.tf_answer is None:
        await callback.answer("Ошибка: вопрос не найден.")
        return

    is_correct = (question.tf_answer == user_says_true)

    session = await _load_session(state, db)
    if session:
        if is_correct:
            session.correct_count += 1
        session.current_index += 1
        await db.commit()

    correct_label = "Верно" if question.tf_answer else "Неверно"
    verdict = "Правильно." if is_correct else f"Неверно. Правильный ответ: {correct_label}."
    await callback.message.edit_text(f"{callback.message.text}\n\n{verdict}")

    if session:
        await _show_next_question(callback.message, session, db, state)

@router.callback_query(QuizStates.in_session, F.data.startswith("know:"))
async def handle_know(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    _, q_id_s, verdict = callback.data.split(":")
    q_id = int(q_id_s)
    knows = verdict == "yes"

    session = await _load_session(state, db)
    if session:
        if knows:
            session.correct_count += 1
        session.current_index += 1
        await db.commit()

    if knows:
        await callback.message.edit_text(f"{callback.message.text}\n\nОтмечено: Знаю.")
        if session:
            await _show_next_question(callback.message, session, db, state)
    else:
        question = await db.scalar(select(Question).where(Question.id == q_id))
        answer_text = ""
        if question:
            if question.reference_answer:
                answer_text = question.reference_answer
            else:
                correct = await db.scalar(
                    select(AnswerOption).where(
                        AnswerOption.question_id == q_id,
                        AnswerOption.is_correct == True,
                    )
                )
                if correct:
                    answer_text = correct.text

        await callback.message.edit_text(
            f"{callback.message.text}\n\nОтмечено: Не знаю."
            + (f"\n\nОтвет: {answer_text}" if answer_text else ""),
            reply_markup=next_question_keyboard(),
        )


@router.callback_query(QuizStates.in_session, F.data == "next_question")
async def handle_next_question(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    session = await _load_session(state, db)
    if session:
        await callback.message.edit_reply_markup(reply_markup=None)
        await _show_next_question(callback.message, session, db, state)
    else:
        await callback.message.answer("Сессия не найдена.", reply_markup=main_menu_keyboard())

@router.callback_query(QuizStates.in_session, F.data == "cancel_quiz")
async def cancel_quiz(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    session = await _load_session(state, db)
    if session:
        session.is_complete = True
        await db.commit()
    await state.clear()
    await callback.message.edit_text("Тренировка завершена досрочно.", reply_markup=main_menu_keyboard())

async def _finish_session(
    message: Message, session: TrainingSession, db: AsyncSession, state: FSMContext
) -> None:
    session.is_complete = True
    await db.commit()
    await state.clear()

    total, correct = session.total_count, session.correct_count
    pct = round(correct / total * 100) if total else 0

    if pct >= 80:
        grade = "Отлично."
    elif pct >= 60:
        grade = "Хорошо."
    elif pct >= 40:
        grade = "Стоит повторить."
    else:
        grade = "Продолжайте тренироваться."

    await message.answer(
        f"Тренировка завершена.\n\n"
        f"Правильных ответов: {correct} из {total} ({pct}%)\n\n{grade}",
        reply_markup=main_menu_keyboard(),
    )

def _filter_questions(questions: list[Question], mode: str) -> list[Question]:
    """
    self_eval - только tf-утверждения.
    Все остальные режимы - только обычные вопросы.
    Это единственная фильтрация: количество одинаково для любого обычного режима.
    """
    if mode == "self_eval":
        return [q for q in questions if q.tf_answer is not None]
    return [q for q in questions if q.tf_answer is None]


def _pick_format(question: Question, mode: str) -> str:
    if mode == "self_eval":
        return "self_eval"

    if mode == "marathon":
        return "open" if question.is_open else "closed"

    formats = mode.split(",")

    if len(formats) == 1:
        fmt = formats[0]
        if fmt == "closed" and (question.is_open or not question.options):
            return "self_eval"
        if fmt == "open" and not question.reference_answer:
            return "self_eval"
        return fmt

    compatible = []
    if "closed" in formats and not question.is_open and question.options:
        compatible.append("closed")
    if "open" in formats and question.reference_answer:
        compatible.append("open")
    if "self_eval" in formats:
        compatible.append("self_eval")

    return random.choice(compatible) if compatible else "self_eval"


async def _load_session(state: FSMContext, db: AsyncSession) -> TrainingSession | None:
    data = await state.get_data()
    sid = data.get("session_id")
    if not sid:
        return None
    return await db.scalar(select(TrainingSession).where(TrainingSession.id == sid))


async def _load_questions_by_ids(ids: list[int], db: AsyncSession) -> list[Question]:
    if not ids:
        return []
    result = await db.execute(
        select(Question)
        .where(Question.id.in_(ids))
        .options(selectinload(Question.options))
    )
    by_id = {q.id: q for q in result.scalars().all()}
    return [by_id[i] for i in ids if i in by_id]


async def _get_user(telegram_id: int, db: AsyncSession) -> User | None:
    return await db.scalar(select(User).where(User.telegram_id == telegram_id))
