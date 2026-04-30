from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    BOT_TOKEN: str
    OPENAI_API_KEY: str
    OPENAI_BASE_URL: str | None = None
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/interview_bot.db"
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    MAX_FILE_SIZE_BYTES: int = 10 * 1024 * 1024
    QUESTIONS_PER_DOCUMENT: int = 30
    TEXTS_DIR: str = "./data/texts"
    VECTORS_DIR: str = "./data/vectors"
    CHAT_HISTORY_WINDOW: int = 6


settings = Settings()
