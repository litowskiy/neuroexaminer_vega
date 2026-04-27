"""
Загрузка учебного материала.

Алгоритм:
1. Принять файл, проверить формат и размер.
2. Извлечь текст.
3. Вычислить SHA-256 хэш — если такой документ уже обработан, вернуть его вопросы из БД.
4. Иначе — вызвать OpenAI API, сохранить вопросы.
"""
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import main_menu_keyboard
from bot.states import UploadStates
from config import settings
from database.models import AnswerOption, Document, Question, User
from services.file_processor import compute_hash, extract_text
from services.question_generator import generate_questions_from_text, generate_tf_statements

logger = logging.getLogger(__name__)
router = Router()

ALLOWED_EXTENSIONS = {"pdf", "docx", "txt", "md"}


@router.callback_query(F.data == "upload")
async def upload_start(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(UploadStates.waiting_for_file)
    size_mb = settings.MAX_FILE_SIZE_BYTES // 1024 // 1024
    await callback.message.edit_text(
        f"Загрузка материала\n\n"
        f"Отправьте файл с учебным материалом.\n"
        f"Форматы: PDF, DOCX, TXT, MD\n"
        f"Максимальный размер: {size_mb} МБ\n\n"
        "Команда /menu — отменить.",
    )


@router.message(UploadStates.waiting_for_file, F.document)
async def handle_file(message: Message, state: FSMContext, bot: Bot, db: AsyncSession) -> None:
    doc = message.document
    filename = doc.file_name or "file.txt"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in ALLOWED_EXTENSIONS:
        await message.answer(
            f"Формат «{ext}» не поддерживается.\n"
            "Поддерживаются: PDF, DOCX, TXT, MD."
        )
        return

    if doc.file_size > settings.MAX_FILE_SIZE_BYTES:
        size_mb = settings.MAX_FILE_SIZE_BYTES // 1024 // 1024
        await message.answer(f"Файл слишком большой. Максимум {size_mb} МБ.")
        return

    await state.clear()
    status_msg = await message.answer("Скачиваю файл...")

    file_info = await bot.get_file(doc.file_id)
    file_bytes = await bot.download_file(file_info.file_path)
    content = file_bytes.read()

    await status_msg.edit_text("Извлекаю текст...")
    try:
        text = extract_text(content, filename)
    except Exception as exc:
        logger.error("Text extraction failed for %s: %s", filename, exc)
        await status_msg.edit_text(
            f"Не удалось извлечь текст из файла.\nОшибка: {type(exc).__name__}: {exc}",
            reply_markup=main_menu_keyboard(),
        )
        return

    if len(text) < 100:
        await status_msg.edit_text(
            "В файле слишком мало текста для генерации вопросов.",
            reply_markup=main_menu_keyboard(),
        )
        return

    text_hash = compute_hash(text)
    existing_doc = await db.scalar(
        select(Document).where(
            Document.text_hash == text_hash,
            Document.status == "ready",
        )
    )
    if existing_doc:
        q_count = await db.scalar(
            select(Document).where(Document.id == existing_doc.id)
        )
        questions = (await db.execute(
            select(Question).where(Question.document_id == existing_doc.id)
        )).scalars().all()
        await status_msg.edit_text(
            f"Этот материал уже был обработан ранее.\n\n"
            f"Файл: {existing_doc.filename}\n"
            f"Вопросов: {len(questions)}\n\n"
            "Перейдите в «Мои материалы» для тренировки.",
            reply_markup=main_menu_keyboard(),
        )
        return

    user = await db.scalar(select(User).where(User.telegram_id == message.from_user.id))
    if not user:
        await status_msg.edit_text("Ошибка: запустите бота командой /start.")
        return

    document = Document(
        user_id=user.id, filename=filename, text_hash=text_hash, status="processing"
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)

    await status_msg.edit_text(
        "Генерирую вопросы через OpenAI...\n"
        "Обычно занимает 20–40 секунд."
    )

    try:
        raw_questions = await generate_questions_from_text(
            text, count=settings.QUESTIONS_PER_DOCUMENT
        )
    except Exception as exc:
        logger.error("Question generation failed: %s", exc)
        document.status = "failed"
        await db.commit()

        err_type = type(exc).__name__
        err_msg = str(exc)
        if "401" in err_msg or "invalid_api_key" in err_msg:
            hint = "Неверный ключ OpenAI API. Проверьте значение OPENAI_API_KEY в файле .env."
        elif "429" in err_msg or "rate_limit" in err_msg:
            hint = "Превышен лимит запросов к OpenAI. Попробуйте через несколько минут."
        elif "Connection" in err_type or "Network" in err_type:
            hint = "Нет соединения с OpenAI API. Проверьте интернет-соединение."
        else:
            hint = f"Ошибка API: {err_type}"

        await status_msg.edit_text(
            f"Не удалось сгенерировать вопросы.\n\n{hint}",
            reply_markup=main_menu_keyboard(),
        )
        return

    if not raw_questions:
        document.status = "failed"
        await db.commit()
        await status_msg.edit_text(
            "ИИ не вернул вопросы. Возможно, текст материала слишком короткий или неструктурированный.",
            reply_markup=main_menu_keyboard(),
        )
        return

    saved = 0
    for q_data in raw_questions:
        question = Question(
            text=q_data["text"],
            category="custom",
            is_open=False,
            reference_answer=q_data.get("reference_answer"),
            document_id=document.id,
        )
        db.add(question)
        await db.flush()

        for i, opt in enumerate(q_data.get("options", [])):
            db.add(AnswerOption(
                question_id=question.id,
                text=opt["text"],
                is_correct=opt.get("is_correct", False),
                order=i,
            ))
        saved += 1

    await status_msg.edit_text("Генерирую утверждения для режима Верно/Неверно...")
    tf_saved = 0
    try:
        tf_data = await generate_tf_statements(text, count=10)
        for stmt in tf_data:
            if "tf_answer" not in stmt:
                continue
            db.add(Question(
                text=stmt["text"],
                category="custom",
                is_open=False,
                tf_answer=bool(stmt["tf_answer"]),
                document_id=document.id,
            ))
            tf_saved += 1
    except Exception as exc:
        logger.warning("TF statement generation failed (non-fatal): %s", exc)

    document.status = "ready"
    await db.commit()

    await status_msg.edit_text(
        f"Готово. По материалу «{filename}» сгенерировано:\n"
        f"— {saved} вопросов (закрытые + открытые)\n"
        f"— {tf_saved} утверждений (Верно/Неверно)\n\n"
        "Перейдите в «Мои материалы» для тренировки.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(UploadStates.waiting_for_file)
async def handle_non_file(message: Message) -> None:
    await message.answer(
        "Пожалуйста, отправьте файл. Команда /menu — отменить."
    )
