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
    is_teacher = Column(Boolean, default=False)
    full_name = Column(String(256), nullable=True)
    subject = Column(String(128), nullable=True)
    eval_strictness = Column(String(16), default="standard")  # soft / standard / strict
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
    assignment_id = Column(Integer, ForeignKey("assignments.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="sessions")

    def get_question_ids(self) -> list[int]:
        return json.loads(self.question_ids)

    def set_question_ids(self, ids: list[int]) -> None:
        self.question_ids = json.dumps(ids)
        self.total_count = len(ids)


class Group(Base):
    """Учебная группа, созданная преподавателем. code — промокод для вступления."""
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String(128), nullable=False)
    code = Column(String(16), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    teacher = relationship("User")
    members = relationship("GroupMember", back_populates="group", cascade="all, delete-orphan")
    assignments = relationship("Assignment", back_populates="group", cascade="all, delete-orphan")


class GroupMember(Base):
    """Участник группы. ФИО заполняется при вступлении."""
    __tablename__ = "group_members"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    full_name = Column(String(256), nullable=False)
    joined_at = Column(DateTime, default=datetime.utcnow)

    group = relationship("Group", back_populates="members")
    user = relationship("User")


class Assignment(Base):
    """Тест, назначенный преподавателем группе (вопросы берутся из документа)."""
    __tablename__ = "assignments"

    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    title = Column(String(256), nullable=False)
    mode = Column(String(32), default="closed")
    question_count = Column(Integer, default=10)
    created_at = Column(DateTime, default=datetime.utcnow)

    group = relationship("Group", back_populates="assignments")
    document = relationship("Document")


class AnswerRecord(Base):
    """Ответ на конкретный вопрос внутри сессии. Нужен для апелляций и отчётов."""
    __tablename__ = "answer_records"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("training_sessions.id"), nullable=False)
    question_id = Column(Integer, ForeignKey("questions.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    user_answer = Column(Text, nullable=True)
    is_correct = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("TrainingSession")
    question = relationship("Question")
    user = relationship("User")


class Appeal(Base):
    """Апелляция студента на оценку открытого ответа."""
    __tablename__ = "appeals"

    id = Column(Integer, primary_key=True)
    record_id = Column(Integer, ForeignKey("answer_records.id"), nullable=False)
    status = Column(String(16), default="pending")  # pending / approved / rejected
    created_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)

    record = relationship("AnswerRecord")
