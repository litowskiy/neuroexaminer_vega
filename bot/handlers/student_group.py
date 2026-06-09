"""
Студенческая часть групповой работы:
- вступление в группу по промокоду + ввод ФИО
- список тестов от преподавателя и их прохождение
- подача апелляции на оценку открытого ответа
"""
import logging
import random

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.handlers.start import get_or_create_user
from bot.handlers.quiz import _create_and_start_session, _filter_questions
from bot.keyboards import main_menu_keyboard, student_assignments_keyboard
from bot.states import JoinGroupStates
from database.models import (
    AnswerRecord,
    Appeal,
    Assignment,
    Group,
    GroupMember,
    Question,
    TrainingSession,
)

logger = logging.getLogger(__name__)
router = Router()


# ---------- Вступление в группу ----------

@router.callback_query(F.data == "join_group")
async def join_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(JoinGroupStates.waiting_code)
    await callback.message.edit_text(
        "Введите промокод группы, который вам дал преподаватель.\n\n"
        "Команда /menu — отменить."
    )


@router.message(JoinGroupStates.waiting_code, F.text)
async def join_code(message: Message, state: FSMContext, db: AsyncSession) -> None:
    code = message.text.strip().upper()
    group = await db.scalar(select(Group).where(Group.code == code))
    if not group:
        await message.answer("Группа с таким промокодом не найдена. Проверьте код и попробуйте ещё раз:")
        return

    user = await get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name, db,
    )
    existing = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group.id,
            GroupMember.user_id == user.id,
        )
    )
    if existing:
        await state.clear()
        await message.answer(
            f"Вы уже состоите в группе «{group.name}».",
            reply_markup=main_menu_keyboard(),
        )
        return

    await state.update_data(join_group_id=group.id)
    await state.set_state(JoinGroupStates.waiting_fio)
    await message.answer(
        f"Группа «{group.name}» найдена.\n\n"
        "Введите ваше ФИО (например: Иванов Иван Иванович):"
    )


@router.message(JoinGroupStates.waiting_fio, F.text)
async def join_fio(message: Message, state: FSMContext, db: AsyncSession) -> None:
    full_name = " ".join(message.text.split())[:256]
    if len(full_name) < 5 or len(full_name.split()) < 2:
        await message.answer("Введите полное ФИО (минимум фамилия и имя):")
        return

    data = await state.get_data()
    group = await db.scalar(select(Group).where(Group.id == data["join_group_id"]))
    if not group:
        await state.clear()
        await message.answer("Группа не найдена.", reply_markup=main_menu_keyboard())
        return

    user = await get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name, db,
    )
    db.add(GroupMember(group_id=group.id, user_id=user.id, full_name=full_name))
    await db.commit()
    await state.clear()

    await message.answer(
        f"Вы вступили в группу «{group.name}» как {full_name}.\n\n"
        "Тесты от преподавателя появятся в соответствующем разделе меню.",
        reply_markup=main_menu_keyboard(),
    )


# ---------- Тесты от преподавателя ----------

@router.callback_query(F.data == "teacher_tests")
async def teacher_tests(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    await state.clear()
    user = await get_or_create_user(
        callback.from_user.id, callback.from_user.username, callback.from_user.first_name, db,
    )
    memberships = (await db.execute(
        select(GroupMember).where(GroupMember.user_id == user.id)
    )).scalars().all()

    if not memberships:
        await callback.message.edit_text(
            "Вы пока не состоите ни в одной группе.\n\n"
            "Вступите в группу по промокоду от преподавателя.",
            reply_markup=main_menu_keyboard(),
        )
        return

    group_ids = [m.group_id for m in memberships]
    assignments = (await db.execute(
        select(Assignment, Group.name)
        .join(Group, Assignment.group_id == Group.id)
        .where(Assignment.group_id.in_(group_ids))
        .order_by(Assignment.created_at.desc())
    )).all()

    if not assignments:
        await callback.message.edit_text(
            "Преподаватель пока не назначил тестов.",
            reply_markup=main_menu_keyboard(),
        )
        return

    items = []
    for asg, group_name in assignments:
        done = await db.scalar(
            select(TrainingSession).where(
                TrainingSession.assignment_id == asg.id,
                TrainingSession.user_id == user.id,
                TrainingSession.is_complete == True,
            )
        )
        mark = " [пройден]" if done else ""
        items.append((asg.id, f"{asg.title[:30]} — {group_name[:15]}{mark}"))

    await callback.message.edit_text(
        "Тесты от преподавателя:",
        reply_markup=student_assignments_keyboard(items),
    )


@router.callback_query(F.data.startswith("asg:"))
async def start_assignment(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    asg_id = int(callback.data.split(":")[1])
    assignment = await db.scalar(select(Assignment).where(Assignment.id == asg_id))
    if not assignment:
        await callback.answer("Тест не найден.", show_alert=True)
        return

    user = await get_or_create_user(
        callback.from_user.id, callback.from_user.username, callback.from_user.first_name, db,
    )

    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == assignment.group_id,
            GroupMember.user_id == user.id,
        )
    )
    if not member:
        await callback.answer("Вы не состоите в этой группе.", show_alert=True)
        return

    done = await db.scalar(
        select(TrainingSession).where(
            TrainingSession.assignment_id == assignment.id,
            TrainingSession.user_id == user.id,
            TrainingSession.is_complete == True,
        )
    )
    if done:
        pct = round(done.correct_count / done.total_count * 100) if done.total_count else 0
        await callback.answer(
            f"Тест уже пройден: {done.correct_count}/{done.total_count} ({pct}%).",
            show_alert=True,
        )
        return

    questions = (await db.execute(
        select(Question)
        .where(Question.document_id == assignment.document_id)
        .options(selectinload(Question.options))
    )).scalars().all()

    filtered = _filter_questions(list(questions), assignment.mode)
    if not filtered:
        await callback.answer("В тесте нет вопросов.", show_alert=True)
        return

    random.shuffle(filtered)
    await _create_and_start_session(
        callback.message, user, filtered, db, state,
        topic_label=f"Тест: {assignment.title}",
        mode=assignment.mode,
        marathon=False,
        options_count=4,
        assignment_id=assignment.id,
    )


# ---------- Апелляция ----------

@router.callback_query(F.data.startswith("appeal:"))
async def submit_appeal(callback: CallbackQuery, db: AsyncSession) -> None:
    record_id = int(callback.data.split(":")[1])
    record = await db.scalar(select(AnswerRecord).where(AnswerRecord.id == record_id))
    if not record:
        await callback.answer("Ответ не найден.", show_alert=True)
        return

    existing = await db.scalar(select(Appeal).where(Appeal.record_id == record_id))
    if existing:
        await callback.answer("Апелляция по этому вопросу уже подана.", show_alert=True)
        return

    db.add(Appeal(record_id=record_id))
    await db.commit()

    await callback.answer("Апелляция отправлена преподавателю.", show_alert=True)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
