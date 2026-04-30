"""
RAG-сервис: чанкинг текста, построение FAISS-индекса, ответы с источниками.

Индексы: data/vectors/{text_hash}/
Тексты:  data/texts/{text_hash}.txt
"""
import asyncio
import logging
from pathlib import Path

from langchain.chains import ConversationalRetrievalChain
from langchain_community.vectorstores import FAISS
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from config import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
RETRIEVAL_K = 4


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
    kwargs: dict = dict(
        api_key=settings.OPENAI_API_KEY,
        model=settings.OPENAI_EMBEDDING_MODEL,
    )
    if settings.OPENAI_BASE_URL:
        kwargs["openai_api_base"] = settings.OPENAI_BASE_URL
    return OpenAIEmbeddings(**kwargs)


def _make_llm() -> ChatOpenAI:
    kwargs: dict = dict(
        model=settings.OPENAI_MODEL,
        api_key=settings.OPENAI_API_KEY,
        temperature=0.3,
    )
    if settings.OPENAI_BASE_URL:
        kwargs["base_url"] = settings.OPENAI_BASE_URL
    return ChatOpenAI(**kwargs)


def _build_index_sync(text: str, text_hash: str) -> None:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = splitter.split_text(text)
    store = FAISS.from_texts(chunks, _make_embeddings())
    idx_dir = _index_path(text_hash)
    idx_dir.mkdir(parents=True, exist_ok=True)
    store.save_local(str(idx_dir))
    logger.info("FAISS index built: %d chunks → %s", len(chunks), idx_dir)


async def build_index(text: str, text_hash: str) -> None:
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _build_index_sync, text, text_hash)


def _load_store(text_hash: str) -> FAISS:
    return FAISS.load_local(
        str(_index_path(text_hash)),
        _make_embeddings(),
        allow_dangerous_deserialization=True,
    )


def _answer_sync(
    text_hash: str,
    question: str,
    history: list[tuple[str, str]],
) -> tuple[str, list[str]]:
    store = _load_store(text_hash)
    chain = ConversationalRetrievalChain.from_llm(
        llm=_make_llm(),
        retriever=store.as_retriever(search_kwargs={"k": RETRIEVAL_K}),
        return_source_documents=True,
        verbose=False,
    )
    window = history[-(settings.CHAT_HISTORY_WINDOW):]
    result = chain.invoke({"question": question, "chat_history": window})
    answer = result["answer"]
    sources = [doc.page_content for doc in result.get("source_documents", [])]
    return answer, sources


async def answer_with_sources(
    text_hash: str,
    question: str,
    history: list[tuple[str, str]],
) -> tuple[str, list[str]]:
    """
    RAG-ответ через ConversationalRetrievalChain.
    Возвращает (ответ, список чанков-источников).
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _answer_sync, text_hash, question, history)


def _retrieve_sync(text_hash: str, query: str, k: int = RETRIEVAL_K) -> list[str]:
    store = _load_store(text_hash)
    docs = store.similarity_search(query, k=k)
    return [d.page_content for d in docs]


async def retrieve_chunks(text_hash: str, query: str, k: int = RETRIEVAL_K) -> list[str]:
    """Возвращает список релевантных чанков (для тьютора)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _retrieve_sync, text_hash, query, k)
