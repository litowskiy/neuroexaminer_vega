"""
RAG-сервис: чанкинг текста, построение FAISS-индекса, ответы на вопросы.

Индексы хранятся на диске: data/vectors/{text_hash}/
Тексты документов: data/texts/{text_hash}.txt
"""
import asyncio
import logging
from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings
from services.question_generator import client as openai_client

logger = logging.getLogger(__name__)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
RETRIEVAL_K = 5

_SYSTEM_PROMPT = (
    "Ты — умный помощник для изучения учебного материала. "
    "Отвечай ТОЛЬКО на основе предоставленных фрагментов документа. "
    "Если ответа нет в материале — честно скажи об этом. "
    "Отвечай подробно, структурированно и на русском языке."
)


def _texts_path(text_hash: str) -> Path:
    return Path(settings.TEXTS_DIR) / f"{text_hash}.txt"


def _index_path(text_hash: str) -> Path:
    return Path(settings.VECTORS_DIR) / text_hash


def save_document_text(text: str, text_hash: str) -> None:
    path = _texts_path(text_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_document_text(text_hash: str) -> str | None:
    path = _texts_path(text_hash)
    return path.read_text(encoding="utf-8") if path.exists() else None


def index_exists(text_hash: str) -> bool:
    return (_index_path(text_hash) / "index.faiss").exists()


def _make_embeddings() -> OpenAIEmbeddings:
    kwargs = dict(
        api_key=settings.OPENAI_API_KEY,
        model=settings.OPENAI_EMBEDDING_MODEL,
    )
    if settings.OPENAI_BASE_URL:
        kwargs["openai_api_base"] = settings.OPENAI_BASE_URL
    return OpenAIEmbeddings(**kwargs)


def _build_index_sync(text: str, text_hash: str) -> None:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_text(text)
    embeddings = _make_embeddings()
    store = FAISS.from_texts(chunks, embeddings)
    idx_dir = _index_path(text_hash)
    idx_dir.mkdir(parents=True, exist_ok=True)
    store.save_local(str(idx_dir))
    logger.info("FAISS index built: %d chunks → %s", len(chunks), idx_dir)


async def build_index(text: str, text_hash: str) -> None:
    """Строит FAISS-индекс асинхронно (в пуле потоков)."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _build_index_sync, text, text_hash)


def _load_store(text_hash: str) -> FAISS:
    embeddings = _make_embeddings()
    return FAISS.load_local(
        str(_index_path(text_hash)),
        embeddings,
        allow_dangerous_deserialization=True,
    )


def _retrieve_sync(text_hash: str, question: str, k: int = RETRIEVAL_K) -> list[str]:
    store = _load_store(text_hash)
    docs = store.similarity_search(question, k=k)
    return [d.page_content for d in docs]


async def retrieve_chunks(text_hash: str, question: str) -> list[str]:
    """Возвращает список релевантных чанков из индекса."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _retrieve_sync, text_hash, question)


async def answer_question(
    text_hash: str,
    question: str,
    history: list[tuple[str, str]],
) -> str:
    """
    RAG-ответ: находит релевантные чанки, формирует промпт с историей,
    вызывает OpenAI и возвращает строку-ответ.

    history — список пар (вопрос_пользователя, ответ_бота), последние N диалогов.
    """
    chunks = await retrieve_chunks(text_hash, question)
    context = "\n\n---\n\n".join(chunks)

    messages: list[dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]

    messages.append({
        "role": "user",
        "content": f"Фрагменты учебного материала:\n\n{context}",
    })
    messages.append({
        "role": "assistant",
        "content": "Понял, буду отвечать строго на основе этих фрагментов.",
    })

    window = history[-(settings.CHAT_HISTORY_WINDOW):]
    for user_q, bot_a in window:
        messages.append({"role": "user", "content": user_q})
        messages.append({"role": "assistant", "content": bot_a})

    messages.append({"role": "user", "content": question})

    response = await openai_client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=messages,
        temperature=0.3,
        max_tokens=1024,
    )
    return response.choices[0].message.content.strip()
