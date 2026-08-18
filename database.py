import os
import datetime
from sqlalchemy import (
    create_engine, Column, Integer, BigInteger, String, Text, Boolean,
    DateTime, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./edubot.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class GroupSettings(Base):
    __tablename__ = "group_settings"
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, unique=True, index=True)
    welcome_enabled = Column(Boolean, default=True)
    welcome_text = Column(Text, default="أهلاً بيك يا {name} في الجروب! 🎓\nمنور معانا في رحلة الثانوية العامة.")
    rules_text = Column(Text, default="١- الاحترام المتبادل.\n٢- ممنوع السبام والإعلانات.\n٣- الالتزام بموضوع الجروب (تعليمي فقط).")
    max_warnings = Column(Integer, default=3)
    lock_links = Column(Boolean, default=False)
    lock_forward = Column(Boolean, default=False)
    lock_stickers = Column(Boolean, default=False)


class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, index=True)
    name = Column(String, default="")
    added_at = Column(DateTime, default=datetime.datetime.utcnow)


class Warning(Base):
    __tablename__ = "warnings"
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    user_id = Column(BigInteger, index=True)
    reason = Column(String, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class FilterWord(Base):
    __tablename__ = "filter_words"
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    trigger = Column(String)
    reply = Column(Text, default="")
    action = Column(String, default="delete")  # delete | delete_warn


class Note(Base):
    __tablename__ = "notes"
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    keyword = Column(String, index=True)
    content = Column(Text)


class Subject(Base):
    __tablename__ = "subjects"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True)
    questions = relationship("Question", back_populates="subject", cascade="all, delete-orphan")


class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True)
    subject_id = Column(Integer, ForeignKey("subjects.id"))
    text = Column(Text, nullable=False)
    options = Column(Text, nullable=False)  # JSON list as string
    correct_index = Column(Integer, nullable=False)
    explanation = Column(Text, default="")
    difficulty = Column(String, default="medium")  # easy|medium|hard
    subject = relationship("Subject", back_populates="questions")


class Exam(Base):
    __tablename__ = "exams"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    subject_id = Column(Integer, ForeignKey("subjects.id"), nullable=True)
    question_ids = Column(Text, default="[]")  # JSON list
    time_per_question = Column(Integer, default=30)  # seconds
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class ExamResult(Base):
    __tablename__ = "exam_results"
    id = Column(Integer, primary_key=True)
    exam_id = Column(Integer, ForeignKey("exams.id"))
    chat_id = Column(BigInteger)
    user_id = Column(BigInteger)
    user_name = Column(String, default="")
    score = Column(Integer, default=0)
    total = Column(Integer, default=0)
    finished_at = Column(DateTime, default=datetime.datetime.utcnow)


class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    content = Column(Text)
    run_at = Column(DateTime, nullable=True)  # لمرة واحدة
    cron_expr = Column(String, nullable=True)  # لو متكررة (مثال: "0 18 * * *")
    is_active = Column(Boolean, default=True)
    pin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_session():
    return SessionLocal()
