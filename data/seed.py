"""
Загрузка базовых вопросов из base_questions.json в SQLite при первом запуске.

Вопросы с document_id=NULL считаются встроенными.
Повторный запуск безопасен: вопросы по каждой теме добавляются только если их ещё нет.
"""
import json
import logging
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AnswerOption, Question

logger = logging.getLogger(__name__)
DATA_FILE = Path(__file__).parent / "base_questions.json"


async def seed_base_questions(db: AsyncSession) -> None:
    with open(DATA_FILE, encoding="utf-8") as f:
        data: dict[str, list[dict]] = json.load(f)

    for category, questions in data.items():
        existing = await db.scalar(
            select(func.count()).select_from(Question).where(
                Question.category == category,
                Question.document_id.is_(None),
            )
        )
        if existing:
            logger.debug("Category '%s' already seeded (%d questions), skipping.", category, existing)
            continue

        for q_data in questions:
            if "tf_answer" in q_data:
                db.add(Question(
                    text=q_data["text"],
                    category=category,
                    is_open=False,
                    tf_answer=bool(q_data["tf_answer"]),
                    document_id=None,
                ))
                continue

            question = Question(
                text=q_data["text"],
                category=category,
                is_open=q_data.get("is_open", False),
                reference_answer=q_data.get("reference_answer"),
                document_id=None,
            )
            db.add(question)
            await db.flush()

            if not q_data.get("is_open"):
                for i, opt in enumerate(q_data.get("options", [])):
                    db.add(AnswerOption(
                        question_id=question.id,
                        text=opt["text"],
                        is_correct=opt.get("is_correct", False),
                        order=i,
                    ))

        logger.info("Seeded %d questions for category '%s'.", len(questions), category)

    await db.commit()
