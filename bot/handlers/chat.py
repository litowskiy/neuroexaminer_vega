"""
Режим «Задать вопросы по материалу» (RAG-чат).

Использует ConversationalRetrievalChain с return_source_documents=True,
показывает цитаты из документа после каждого ответа.
"""
import html
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import main_menu_keyboard, stop_chat_keyboard
from bot.states import ChatStates
from database.models import Document
from services.vector_store import (
    answer_with_sources,
    build_index,
    index_exists,
    load_document_text,
)

logger = logging.getLogger(__name__)
router = Router()

SOURCE_PREVIEW_LEN = 160


def _format_sources(chunks: list[str]) -> str:
    if not chunks:
        return ""
    lines = []
    for chunk in chunks[:3]:
        preview = chunk.strip().replace("\n", " ")[:SOURCE_PREVIEW_LEN]
        lines.append(f"<i>«{html.escape(preview)}…»</i>")
    return "\n\n<b>Источники:</b>\n" + "\n".join(lines)


@router.callback_query(F.data.startswith("mat_chat:"))
async def start_chat(
    callback: CallbackQuery, state: FSMContext, db: AsyncSession
) -> None:
    doc_id = int(callback.data.split(":", 1)[1])

    doc = await db.scalar(select(Document).where(Document.id == doc_id))
    if not doc or doc.status != "ready":
        await callback.answer("Материал недоступен.", show_alert=True)
        return

    text_hash = doc.text_hash

    if not index_exists(text_hash):
        text = load_document_text(text_hash)
        if not text:
            await callback.message.edit_text(
                "Текст документа не найден на сервере.\n"
                "Пожалуйста, загрузите файл заново.",
                reply_markup=main_menu_keyboard(),
            )
            return

        status_msg = await callback.message.edit_text(
            "Подготавливаю документ для поиска...\n"
            "Обычно занимает 10–20 секунд."
        )
        try:
            await build_index(text, text_hash)
        except Exception as exc:
            logger.error("Failed to build FAISS index for %s: %s", text_hash, exc)
            await status_msg.edit_text(
                f"Не удалось создать поисковый индекс.\nОшибка: {exc}",
                reply_markup=main_menu_keyboard(),
            )
            return
        await status_msg.edit_text(
            f"Материал «{doc.filename}» готов.\n\n"
            "Задавайте любые вопросы по тексту документа.\n"
            "Бот ответит и покажет, из каких частей материала взята информация.\n\n"
            "Нажмите «Завершить», чтобы выйти.",
            reply_markup=stop_chat_keyboard(),
        )
    else:
        await callback.message.edit_text(
            f"Материал «{doc.filename}»\n\n"
            "Задавайте любые вопросы — покажу источники из документа.\n\n"
            "Нажмите «Завершить», чтобы выйти.",
            reply_markup=stop_chat_keyboard(),
        )

    await state.set_state(ChatStates.in_chat)
    await state.update_data(
        chat_text_hash=text_hash,
        chat_doc_name=doc.filename,
        chat_history=[],
    )


@router.message(ChatStates.in_chat, F.text)
async def handle_chat_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text_hash: str = data.get("chat_text_hash", "")
    history: list = data.get("chat_history", [])

    if not text_hash:
        await message.answer(
            "Сессия устарела. Нажмите /start и выберите материал заново.",
            reply_markup=main_menu_keyboard(),
        )
        await state.clear()
        return

    thinking_msg = await message.answer("Думаю...")

    try:
        answer, sources = await answer_with_sources(
            text_hash=text_hash,
            question=message.text,
            history=history,
        )
    except Exception as exc:
        logger.error("RAG answer failed: %s", exc)
        await thinking_msg.edit_text(
            f"Не удалось получить ответ: {exc}",
            reply_markup=stop_chat_keyboard(),
        )
        return

    history.append((message.text, answer))
    await state.update_data(chat_history=history)

    sources_text = _format_sources(sources)
    await thinking_msg.edit_text(
        html.escape(answer) + sources_text,
        reply_markup=stop_chat_keyboard(),
    )


@router.callback_query(ChatStates.in_chat, F.data == "stop_chat")
async def stop_chat(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text(
        "Сессия вопросов завершена.\n\nГлавное меню:",
        reply_markup=main_menu_keyboard(),
    )


@router.message(ChatStates.in_chat)
async def handle_non_text_in_chat(message: Message) -> None:
    await message.answer(
        "Пожалуйста, отправляйте только текстовые вопросы.",
        reply_markup=stop_chat_keyboard(),
    )
