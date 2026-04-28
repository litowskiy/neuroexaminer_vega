from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import main_menu_keyboard
from database.models import TrainingSession, User

router = Router()


async def get_or_create_user(
    telegram_id: int,
    username: str | None,
    first_name: str | None,
    db: AsyncSession,
) -> User:
    user = await db.scalar(select(User).where(User.telegram_id == telegram_id))
    if not user:
        user = User(telegram_id=telegram_id, username=username, first_name=first_name)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    return user


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, db: AsyncSession) -> None:
    await state.clear()
    await get_or_create_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        db=db,
    )
    name = message.from_user.first_name or "пользователь"
    await message.answer(
        f"Здравствуйте, {name}.\n\n"
        "Я - бот Нейроэкзаменатор"".\n\n"
        "Со мной ты сможешь подготовиться к важному экзамену или собеседованию!"".\n\n"
        "Возможности:\n"
        "— загрузить учебный материал (PDF, DOCX, TXT, MD)\n"
        "— пройти тренировку по встроенным темам\n"
        "— выбрать режим: тест, открытые вопросы, марафон\n"
        "— смотреть статистику прогресса\n\n"
        "Выберите действие:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery, db: AsyncSession) -> None:
    user = await db.scalar(select(User).where(User.telegram_id == callback.from_user.id))
    if not user:
        await callback.answer("Пользователь не найден.", show_alert=True)
        return

    total_sessions = await db.scalar(
        select(func.count()).select_from(TrainingSession).where(
            TrainingSession.user_id == user.id,
            TrainingSession.is_complete == True,
        )
    ) or 0

    if not total_sessions:
        await callback.message.edit_text(
            "Статистика\n\nЗавершённых тренировок пока нет.",
            reply_markup=main_menu_keyboard(),
        )
        return

    agg = await db.execute(
        select(
            func.sum(TrainingSession.correct_count),
            func.sum(TrainingSession.total_count),
        ).where(
            TrainingSession.user_id == user.id,
            TrainingSession.is_complete == True,
        )
    )
    total_correct, total_questions = agg.one()
    total_correct   = total_correct or 0
    total_questions = total_questions or 0
    pct = round(total_correct / total_questions * 100) if total_questions else 0

    recent = (await db.execute(
        select(TrainingSession)
        .where(
            TrainingSession.user_id == user.id,
            TrainingSession.is_complete == True,
        )
        .order_by(TrainingSession.created_at.desc())
        .limit(5)
    )).scalars().all()

    recent_lines = [
        f"  {s.created_at.strftime('%d.%m')}: {s.correct_count}/{s.total_count} "
        f"({round(s.correct_count / s.total_count * 100) if s.total_count else 0}%)"
        for s in recent
    ]

    await callback.message.edit_text(
        f"Статистика\n\n"
        f"Тренировок завершено: {total_sessions}\n"
        f"Ответов всего: {total_questions}\n"
        f"Правильных: {total_correct} ({pct}%)\n\n"
        f"Последние тренировки:\n" + "\n".join(recent_lines),
        reply_markup=main_menu_keyboard(),
    )
