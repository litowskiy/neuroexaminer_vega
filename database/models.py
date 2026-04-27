import json
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Text, ForeignKey, BigInteger,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(64), nullable=True)
    first_name = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    documents = relationship("Document", back_populates="user")
    sessions = relationship("TrainingSession", back_populates="user")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String(256), nullable=False)
    text_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(32), default="processing")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="documents")
    questions = relationship("Question", back_populates="document")


class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True)
    text = Column(Text, nullable=False)
    category = Column(String(64), nullable=False)
    is_open = Column(Boolean, default=False)
    reference_answer = Column(Text, nullable=True)
    tf_answer = Column(Boolean, nullable=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("Document", back_populates="questions")
    options = relationship(
        "AnswerOption", back_populates="question", cascade="all, delete-orphan",
        order_by="AnswerOption.order",
    )


class AnswerOption(Base):
    __tablename__ = "answer_options"

    id = Column(Integer, primary_key=True)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    text = Column(Text, nullable=False)
    is_correct = Column(Boolean, default=False)
    order = Column(Integer, default=0)

    question = relationship("Question", back_populates="options")


class TrainingSession(Base):
    __tablename__ = "training_sessions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    question_ids = Column(Text, nullable=False)
    mode = Column(String(16), default="mixed")
    current_index = Column(Integer, default=0)
    correct_count = Column(Integer, default=0)
    total_count = Column(Integer, default=0)
    is_complete = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="sessions")

    def get_question_ids(self) -> list[int]:
        return json.loads(self.question_ids)

    def set_question_ids(self, ids: list[int]) -> None:
        self.question_ids = json.dumps(ids)
        self.total_count = len(ids)
