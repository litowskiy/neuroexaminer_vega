from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.handlers.quiz import start_quiz_setup
from bot.handlers.start import get_or_create_user
from bot.keyboards import BUILT_IN_TOPICS, topics_keyboard
from database.models import Question

router = Router()


@router.callback_query(F.data == "topics")
async def show_topics(callback: CallbackQuery, db: AsyncSession) -> None:
    lines = []
    for key, label in BUILT_IN_TOPICS.items():
        count = await db.scalar(
            select(func.count()).select_from(Question).where(
                Question.category == key,
                Question.document_id.is_(None),
            )
        ) or 0
        lines.append(f"{label}: {count} вопросов")

    await callback.message.edit_text(
        "Встроенные темы:\n\n" + "\n".join(lines) + "\n\nВыберите тему:",
        reply_markup=topics_keyboard(),
    )


@router.callback_query(F.data.startswith("topic:"))
async def select_topic(
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
        await callback.answer(f"По теме «{label}» пока нет вопросов.", show_alert=True)
        return

    user = await get_or_create_user(
        telegram_id=callback.from_user.id,
        username=callback.from_user.username,
        first_name=callback.from_user.first_name,
        db=db,
    )
    await start_quiz_setup(callback.message, user, list(questions), db, state, topic_label=label)
