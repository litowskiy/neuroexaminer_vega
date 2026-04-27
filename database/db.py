from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from .models import Base
from config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate(conn)


async def _migrate(conn) -> None:
    """Накатывает отсутствующие колонки без Alembic."""
    migrations = [
        "ALTER TABLE training_sessions ADD COLUMN mode VARCHAR(16) DEFAULT 'mixed'",
        "ALTER TABLE questions ADD COLUMN tf_answer BOOLEAN DEFAULT NULL",
    ]
    for sql in migrations:
        try:
            await conn.execute(text(sql))
        except Exception:
            pass
