"""
Кабинет преподавателя:
- группы (создание с промокодом, список студентов)
- создание тестов из загруженных материалов
- результаты группы (обновление по кнопке + PDF-отчёт)
- апелляции (засчитать 1/0 или отклонить)
"""
import logging
import random
import string

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.start import get_or_create_user
from bot.keyboards import (
    appeal_decision_keyboard,
    appeals_list_keyboard,
    assignment_count_keyboard,
    assignment_mode_keyboard,
    teacher_docs_keyboard,
    teacher_group_keyboard,
    teacher_groups_keyboard,
    teacher_menu_keyboard,
    teacher_results_keyboard,
)
from bot.states import TeacherStates
from database.models import (
    AnswerRecord,
    Appeal,
    Assignment,
    Document,
    Group,
    GroupMember,
    Question,
    TrainingSession,
    User,
)
from services.report import build_group_report

logger = logging.getLogger(__name__)
router = Router()

MODE_LABELS = {
    "closed": "Закрытые (тест)",
    "open": "Открытые вопросы",
    "closed,open": "Смешанный",
}


def _generate_code() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=6))


async def _pending_appeals_count(teacher: User, db: AsyncSession) -> int:
    return await db.scalar(
        select(func.count())
        .select_from(Appeal)
        .join(AnswerRecord, Appeal.record_id == AnswerRecord.id)
        .join(TrainingSession, AnswerRecord.session_id == TrainingSession.id)
        .join(Assignment, TrainingSession.assignment_id == Assignment.id)
        .join(Group, Assignment.group_id == Group.id)
        .where(Appeal.status == "pending", Group.teacher_id == teacher.id)
    ) or 0


# ---------- Меню кабинета ----------

@router.callback_query(F.data == "teacher")
async def teacher_menu(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    await state.clear()
    user = await get_or_create_user(
        callback.from_user.id, callback.from_user.username, callback.from_user.first_name, db,
    )

    if not user.is_teacher:
        await state.set_state(TeacherStates.waiting_auth_code)
        await callback.message.edit_text(
            "Доступ в кабинет преподавателя.\n\n"
            "Введите кодовое слово преподавателя.\n\n"
            "Команда /menu — отменить."
        )
        return

    pending = await _pending_appeals_count(user, db)
    await callback.message.edit_text(
        f"Кабинет преподавателя\n\n"
        f"ФИО: {user.full_name or '—'}\n"
        f"Предмет: {user.subject or '—'}",
        reply_markup=teacher_menu_keyboard(pending),
    )


@router.message(TeacherStates.waiting_auth_code, F.text)
async def teacher_auth_code(message: Message, state: FSMContext) -> None:
    from config import settings
    if message.text.strip() != settings.TEACHER_CODE:
        await message.answer("Неверное кодовое слово. Попробуйте ещё раз или /menu — отменить.")
        return

    await state.set_state(TeacherStates.waiting_fio)
    await message.answer("Кодовое слово принято.\n\nВведите ваше ФИО (например: Иванов Иван Иванович):")


@router.message(TeacherStates.waiting_fio, F.text)
async def teacher_fio(message: Message, state: FSMContext) -> None:
    full_name = " ".join(message.text.split())[:256]
    if len(full_name) < 5 or len(full_name.split()) < 2:
        await message.answer("Введите полное ФИО (минимум фамилия и имя):")
        return

    await state.update_data(teacher_fio=full_name)
    await state.set_state(TeacherStates.waiting_subject)
    await message.answer("Введите название предмета, который вы преподаёте:")


@router.message(TeacherStates.waiting_subject, F.text)
async def teacher_subject(message: Message, state: FSMContext, db: AsyncSession) -> None:
    subject = message.text.strip()[:128]
    if not subject:
        await message.answer("Название предмета не может быть пустым. Введите название:")
        return

    data = await state.get_data()
    user = await get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name, db,
    )
    user.is_teacher = True
    user.full_name = data["teacher_fio"]
    user.subject = subject
    await db.commit()
    await state.clear()

    pending = await _pending_appeals_count(user, db)
    await message.answer(
        f"Регистрация завершена.\n\n"
        f"ФИО: {user.full_name}\n"
        f"Предмет: {user.subject}",
        reply_markup=teacher_menu_keyboard(pending),
    )


# ---------- Группы ----------

@router.callback_query(F.data == "tgroups")
async def teacher_groups(callback: CallbackQuery, state: FSMContext, db: AsyncSession) -> None:
    await state.clear()
    user = await get_or_create_user(
        callback.from_user.id, callback.from_user.username, callback.from_user.first_name, db,
    )
    groups = (await db.execute(
        select(Group).where(Group.teacher_id == user.id).order_by(Group.created_at)
    )).scalars().all()

    text = "Ваши группы:" if groups else "У вас пока нет групп."
    await callback.message.edit_text(text, reply_markup=teacher_groups_keyboard(groups))


@router.callback_query(F.data == "tgroup_new")
async def group_create_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TeacherStates.waiting_group_name)
    await callback.message.edit_text(
        "Введите название новой группы.\n\nКоманда /menu — отменить."
    )


@router.message(TeacherStates.waiting_group_name, F.text)
async def group_create_finish(message: Message, state: FSMContext, db: AsyncSession) -> None:
    name = message.text.strip()[:128]
    if not name:
        await message.answer("Название не может быть пустым. Введите название группы:")
        return

    user = await get_or_create_user(
        message.from_user.id, message.from_user.username, message.from_user.first_name, db,
    )

    code = _generate_code()
    while await db.scalar(select(Group).where(Group.code == code)):
        code = _generate_code()

    group = Group(teacher_id=user.id, name=name, code=code)
    db.add(group)
    await db.commit()
    await db.refresh(group)
    await state.clear()

    await message.answer(
        f"Группа «{name}» создана.\n\n"
        f"Промокод для вступления: {code}\n\n"
        "Передайте его студентам — они вводят код через кнопку «Вступить в группу».",
        reply_markup=teacher_group_keyboard(group.id),
    )


@router.callback_query(F.data.startswith("tgroup:"))
async def group_view(callback: CallbackQuery, db: AsyncSession) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        await callback.answer("Группа не найдена.", show_alert=True)
        return

    members_count = await db.scalar(
        select(func.count()).select_from(GroupMember).where(GroupMember.group_id == group.id)
    ) or 0
    tests_count = await db.scalar(
        select(func.count()).select_from(Assignment).where(Assignment.group_id == group.id)
    ) or 0

    await callback.message.edit_text(
        f"Группа: {group.name}\n"
        f"Промокод: {group.code}\n"
        f"Студентов: {members_count}\n"
        f"Тестов: {tests_count}",
        reply_markup=teacher_group_keyboard(group.id),
    )


@router.callback_query(F.data.startswith("tgstudents:"))
async def group_students(callback: CallbackQuery, db: AsyncSession) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        await callback.answer("Группа не найдена.", show_alert=True)
        return

    members = (await db.execute(
        select(GroupMember).where(GroupMember.group_id == group_id).order_by(GroupMember.full_name)
    )).scalars().all()

    if members:
        lines = [f"{i + 1}. {m.full_name}" for i, m in enumerate(members)]
        text = f"Студенты группы «{group.name}»:\n\n" + "\n".join(lines)
    else:
        text = f"В группе «{group.name}» пока нет студентов.\n\nПромокод: {group.code}"

    await callback.message.edit_text(text, reply_markup=teacher_group_keyboard(group_id))


# ---------- Создание теста ----------

@router.callback_query(F.data.startswith("tgnewtest:"))
async def new_test_pick_doc(callback: CallbackQuery, db: AsyncSession) -> None:
    group_id = int(callback.data.split(":")[1])
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
        await callback.answer(
            "Сначала загрузите материал через «Загрузить материал» — "
            "из него сгенерируются вопросы для теста.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "Выберите материал, из которого собрать тест:",
        reply_markup=teacher_docs_keyboard(group_id, docs),
    )


@router.callback_query(F.data.startswith("tgtestdoc:"))
async def new_test_pick_mode(callback: CallbackQuery) -> None:
    _, group_id, doc_id = callback.data.split(":")
    await callback.message.edit_text(
        "Выберите режим теста:",
        reply_markup=assignment_mode_keyboard(int(group_id), int(doc_id)),
    )


@router.callback_query(F.data.startswith("tgtestmode:"))
async def new_test_pick_count(callback: CallbackQuery, db: AsyncSession) -> None:
    _, group_id_s, doc_id_s, mode = callback.data.split(":")
    group_id, doc_id = int(group_id_s), int(doc_id_s)

    available = await db.scalar(
        select(func.count()).select_from(Question).where(
            Question.document_id == doc_id,
            Question.tf_answer.is_(None),
        )
    ) or 0
    if not available:
        await callback.answer("В этом материале нет вопросов.", show_alert=True)
        return

    await callback.message.edit_text(
        f"Режим: {MODE_LABELS.get(mode, mode)}\n"
        f"Вопросов доступно: {available}\n\n"
        "Сколько вопросов будет в тесте?",
        reply_markup=assignment_count_keyboard(group_id, doc_id, mode, available),
    )


@router.callback_query(F.data.startswith("tgtestcnt:"))
async def new_test_create(callback: CallbackQuery, db: AsyncSession) -> None:
    _, group_id_s, doc_id_s, mode, count_s = callback.data.split(":")
    group_id, doc_id, count = int(group_id_s), int(doc_id_s), int(count_s)

    doc = await db.scalar(select(Document).where(Document.id == doc_id))
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not doc or not group:
        await callback.answer("Материал или группа не найдены.", show_alert=True)
        return

    assignment = Assignment(
        group_id=group_id,
        document_id=doc_id,
        title=doc.filename,
        mode=mode,
        question_count=count,
    )
    db.add(assignment)
    await db.commit()

    await callback.message.edit_text(
        f"Тест создан.\n\n"
        f"Группа: {group.name}\n"
        f"Материал: {doc.filename}\n"
        f"Режим: {MODE_LABELS.get(mode, mode)}\n"
        f"Вопросов: {count}\n\n"
        "Студенты группы увидят его в разделе «Тесты от преподавателя».",
        reply_markup=teacher_group_keyboard(group_id),
    )


# ---------- Результаты ----------

async def _collect_results(group: Group, db: AsyncSession) -> list[dict]:
    members = (await db.execute(
        select(GroupMember).where(GroupMember.group_id == group.id).order_by(GroupMember.full_name)
    )).scalars().all()
    assignments = (await db.execute(
        select(Assignment).where(Assignment.group_id == group.id).order_by(Assignment.created_at)
    )).scalars().all()

    result = []
    for asg in assignments:
        rows = []
        for m in members:
            session = await db.scalar(
                select(TrainingSession).where(
                    TrainingSession.assignment_id == asg.id,
                    TrainingSession.user_id == m.user_id,
                ).order_by(TrainingSession.created_at.desc())
            )
            if not session:
                rows.append({"full_name": m.full_name, "status": "не начат",
                             "correct": None, "total": None})
            elif not session.is_complete:
                rows.append({"full_name": m.full_name, "status": "в процессе",
                             "correct": None, "total": None})
            else:
                rows.append({"full_name": m.full_name, "status": "завершён",
                             "correct": session.correct_count, "total": session.total_count})
        result.append({
            "title": asg.title,
            "mode": MODE_LABELS.get(asg.mode, asg.mode),
            "rows": rows,
        })
    return result


@router.callback_query(F.data.startswith("tgresults:"))
async def group_results(callback: CallbackQuery, db: AsyncSession) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        await callback.answer("Группа не найдена.", show_alert=True)
        return

    results = await _collect_results(group, db)

    if not results:
        text = f"Результаты группы «{group.name}»\n\nТестов пока нет."
    else:
        blocks = []
        for asg in results:
            lines = [f"Тест: {asg['title']} ({asg['mode']})"]
            if not asg["rows"]:
                lines.append("  — студентов в группе нет")
            for row in asg["rows"]:
                if row["correct"] is not None and row["total"]:
                    pct = round(row["correct"] / row["total"] * 100)
                    lines.append(f"  {row['full_name']}: {row['correct']}/{row['total']} ({pct}%)")
                else:
                    lines.append(f"  {row['full_name']}: {row['status']}")
            blocks.append("\n".join(lines))
        text = f"Результаты группы «{group.name}»\n\n" + "\n\n".join(blocks)

    try:
        await callback.message.edit_text(
            text[:4000], reply_markup=teacher_results_keyboard(group_id),
        )
    except TelegramBadRequest:
        await callback.answer("Изменений нет.")
        return
    await callback.answer()


@router.callback_query(F.data.startswith("tgpdf:"))
async def group_pdf(callback: CallbackQuery, db: AsyncSession) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        await callback.answer("Группа не найдена.", show_alert=True)
        return

    teacher = await db.scalar(select(User).where(User.id == group.teacher_id))
    teacher_name = (teacher.full_name or teacher.first_name or teacher.username or "—") if teacher else "—"

    await callback.answer("Формирую отчёт...")
    results = await _collect_results(group, db)

    try:
        pdf_bytes = build_group_report(group.name, teacher_name, results)
    except Exception as exc:
        logger.error("PDF generation failed: %s", exc)
        await callback.message.answer("Не удалось сформировать PDF-отчёт.")
        return

    await callback.message.answer_document(
        BufferedInputFile(pdf_bytes, filename=f"report_{group.code}.pdf"),
        caption=f"Отчёт по группе «{group.name}»",
    )


# ---------- Апелляции ----------

@router.callback_query(F.data == "tappeals")
async def appeals_list(callback: CallbackQuery, db: AsyncSession) -> None:
    user = await get_or_create_user(
        callback.from_user.id, callback.from_user.username, callback.from_user.first_name, db,
    )
    rows = (await db.execute(
        select(Appeal, GroupMember.full_name, Question.text)
        .join(AnswerRecord, Appeal.record_id == AnswerRecord.id)
        .join(TrainingSession, AnswerRecord.session_id == TrainingSession.id)
        .join(Assignment, TrainingSession.assignment_id == Assignment.id)
        .join(Group, Assignment.group_id == Group.id)
        .join(GroupMember, (GroupMember.group_id == Group.id)
              & (GroupMember.user_id == AnswerRecord.user_id))
        .join(Question, AnswerRecord.question_id == Question.id)
        .where(Appeal.status == "pending", Group.teacher_id == user.id)
        .order_by(Appeal.created_at)
    )).all()

    if not rows:
        await callback.message.edit_text(
            "Апелляций, ожидающих рассмотрения, нет.",
            reply_markup=teacher_menu_keyboard(0),
        )
        return

    items = [
        (appeal.id, f"{full_name}: {q_text[:30]}")
        for appeal, full_name, q_text in rows
    ]
    await callback.message.edit_text(
        f"Апелляции на рассмотрении: {len(items)}",
        reply_markup=appeals_list_keyboard(items),
    )


@router.callback_query(F.data.startswith("tappeal:"))
async def appeal_view(callback: CallbackQuery, db: AsyncSession) -> None:
    appeal_id = int(callback.data.split(":")[1])
    appeal = await db.scalar(select(Appeal).where(Appeal.id == appeal_id))
    if not appeal or appeal.status != "pending":
        await callback.answer("Апелляция не найдена или уже рассмотрена.", show_alert=True)
        return

    record = await db.scalar(select(AnswerRecord).where(AnswerRecord.id == appeal.record_id))
    question = await db.scalar(select(Question).where(Question.id == record.question_id))

    mark = "1 (правильно)" if record.is_correct else "0 (неправильно)"
    await callback.message.edit_text(
        f"Апелляция #{appeal.id}\n\n"
        f"Вопрос:\n{question.text}\n\n"
        f"Эталонный ответ:\n{question.reference_answer or '—'}\n\n"
        f"Ответ студента:\n{record.user_answer or '—'}\n\n"
        f"Текущая оценка: {mark}",
        reply_markup=appeal_decision_keyboard(appeal.id),
    )


async def _resolve_appeal(
    appeal: Appeal, db: AsyncSession,
) -> AnswerRecord:
    from datetime import datetime
    appeal.resolved_at = datetime.utcnow()
    record = await db.scalar(select(AnswerRecord).where(AnswerRecord.id == appeal.record_id))
    return record


async def _notify_student(bot: Bot, record: AnswerRecord, db: AsyncSession, text: str) -> None:
    student = await db.scalar(select(User).where(User.id == record.user_id))
    if not student:
        return
    try:
        await bot.send_message(student.telegram_id, text)
    except Exception as exc:
        logger.warning("Failed to notify student %s: %s", student.telegram_id, exc)


@router.callback_query(F.data.startswith("tappeal_set:"))
async def appeal_approve(callback: CallbackQuery, db: AsyncSession, bot: Bot) -> None:
    _, appeal_id_s, mark_s = callback.data.split(":")
    appeal = await db.scalar(select(Appeal).where(Appeal.id == int(appeal_id_s)))
    if not appeal or appeal.status != "pending":
        await callback.answer("Апелляция уже рассмотрена.", show_alert=True)
        return

    new_mark = mark_s == "1"
    appeal.status = "approved"
    record = await _resolve_appeal(appeal, db)

    if record.is_correct != new_mark:
        session = await db.scalar(
            select(TrainingSession).where(TrainingSession.id == record.session_id)
        )
        if session:
            session.correct_count += 1 if new_mark else -1
            session.correct_count = max(0, session.correct_count)
        record.is_correct = new_mark

    await db.commit()

    question = await db.scalar(select(Question).where(Question.id == record.question_id))
    mark_label = "1 (правильно)" if new_mark else "0 (неправильно)"
    await _notify_student(
        bot, record, db,
        f"Ваша апелляция рассмотрена.\n\n"
        f"Вопрос: {question.text[:100]}\n"
        f"Решение: апелляция одобрена, оценка — {mark_label}.",
    )
    await callback.message.edit_text(
        f"Апелляция #{appeal.id} одобрена. Выставлена оценка: {mark_label}.\n"
        "Студент уведомлён.",
        reply_markup=teacher_menu_keyboard(),
    )


@router.callback_query(F.data.startswith("tappeal_reject:"))
async def appeal_reject(callback: CallbackQuery, db: AsyncSession, bot: Bot) -> None:
    appeal_id = int(callback.data.split(":")[1])
    appeal = await db.scalar(select(Appeal).where(Appeal.id == appeal_id))
    if not appeal or appeal.status != "pending":
        await callback.answer("Апелляция уже рассмотрена.", show_alert=True)
        return

    appeal.status = "rejected"
    record = await _resolve_appeal(appeal, db)
    await db.commit()

    question = await db.scalar(select(Question).where(Question.id == record.question_id))
    await _notify_student(
        bot, record, db,
        f"Ваша апелляция рассмотрена.\n\n"
        f"Вопрос: {question.text[:100]}\n"
        f"Решение: апелляция отклонена, оценка осталась прежней.",
    )
    await callback.message.edit_text(
        f"Апелляция #{appeal.id} отклонена. Оценка сохранена.\nСтудент уведомлён.",
        reply_markup=teacher_menu_keyboard(),
    )
