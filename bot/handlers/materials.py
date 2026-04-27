from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.quiz import start_quiz_setup
from bot.handlers.start import get_or_create_user
from bot.keyboards import main_menu_keyboard, materials_keyboard
from database.models import Document, Question, User

router = Router()


@router.callback_query(F.data == "my_materials")
async def show_my_materials(callback: CallbackQuery, db: AsyncSession) -> None:
    user = await db.scalar(select(User).where(User.telegram_id == callback.from_user.id))
    if not user:
        await callback.answer("Запустите бота командой /start.", show_alert=True)
        return

    docs = (await db.execute(
        select(Document)
        .where(Document.user_id == user.id, Document.status == "ready")
        .order_by(Document.created_at.desc())
    )).scalars().all()

    if not docs:
        await callback.message.edit_text(
            "Мои материалы\n\n"
            "Вы ещё не загружали материалы.\n"
            "Нажмите «Загрузить материал», чтобы добавить файл.",
            reply_markup=main_menu_keyboard(),
        )
        return

    lines = []
    for doc in docs:
        q_count = await db.scalar(
            select(func.count()).select_from(Question).where(Question.document_id == doc.id)
        ) or 0
        date_str = doc.created_at.strftime("%d.%m.%Y")
        lines.append(f"{doc.filename} — {q_count} вопр. ({date_str})")

    await callback.message.edit_text(
        "Мои материалы:\n\n" + "\n".join(lines) + "\n\nВыберите материал:",
        reply_markup=materials_keyboard(docs),
    )


@router.callback_query(F.data.startswith("material:"))
async def select_material(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    doc_id = int(callback.data.split(":", 1)[1])

    doc = await db.scalar(select(Document).where(Document.id == doc_id))
    if not doc or doc.status != "ready":
        await callback.answer("Материал не найден или ещё обрабатывается.", show_alert=True)
        return

    questions = (await db.execute(
        select(Question).where(Question.document_id == doc_id)
    )).scalars().all()

    if not questions:
        await callback.answer("По этому материалу нет вопросов.", show_alert=True)
        return

    user = await get_or_create_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        first_name=callback.from_user.first_name,
        db=db,
    )
    await start_quiz_setup(
        callback.message, user, list(questions), db, state, topic_label=doc.filename
    )
