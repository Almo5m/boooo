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
    title = Column(String, default="")
    welcome_enabled = Column(Boolean, default=True)
    welcome_text = Column(Text, default="أهلاً بيك يا {name} في الجروب! 🎓\nمنور معانا في رحلة الثانوية العامة.")
    rules_text = Column(Text, default="١- الاحترام المتبادل.\n٢- ممنوع السبام والإعلانات.\n٣- الالتزام بموضوع الجروب (تعليمي فقط).")
    max_warnings = Column(Integer, default=3)
    lock_links = Column(Boolean, default=False)
    lock_forward = Column(Boolean, default=False)
    lock_stickers = Column(Boolean, default=False)


class BotUser(Base):
    __tablename__ = "bot_users"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, index=True)
    name = Column(String, default="")
    username = Column(String, default="")
    first_seen = Column(DateTime, default=datetime.datetime.utcnow)
    last_seen = Column(DateTime, default=datetime.datetime.utcnow)
    message_count = Column(Integer, default=0)


class ActivityLog(Base):
    __tablename__ = "activity_log"
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    actor_id = Column(BigInteger)
    actor_name = Column(String, default="")
    action = Column(String)  # ban | unban | mute | unmute | kick | warn | reset_warns | pin | unpin | lock | unlock | filter_add | filter_del | note_add | note_del
    target_id = Column(BigInteger, nullable=True)
    target_name = Column(String, default="")
    details = Column(String, default="")
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class StudentPoints(Base):
    __tablename__ = "student_points"
    id = Column(Integer, primary_key=True)
    chat_id = Column(BigInteger, index=True)
    user_id = Column(BigInteger, index=True)
    name = Column(String, default="")
    points = Column(Integer, default=0)
    correct_answers = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow)


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


def _migrate_add_missing_columns():
    """
    ترقية بسيطة لقاعدة بيانات موجودة بالفعل: بتضيف أي عمود جديد اتضاف على
    الموديلات هنا (زي title) من غير ما تمسح أي بيانات قديمة.
    Base.metadata.create_all بيعمل الجداول الناقصة بس، مش الأعمدة الناقصة
    في جدول موجود، فالدالة دي بتغطي الفرق ده.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if table.name not in inspector.get_table_names():
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing_cols:
                continue
            col_type = col.type.compile(engine.dialect)
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col_type}'))


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate_add_missing_columns()


def get_session():
    return SessionLocal()


def log_action(chat_id, actor_id, actor_name, action, target_id=None, target_name="", details=""):
    """بتسجل أي عملية إدارية في سجل النشاط. بتفتح وتقفل الجلسة بنفسها عشان تُستخدم بسطر واحد من أي مكان."""
    session = get_session()
    try:
        session.add(ActivityLog(
            chat_id=chat_id, actor_id=actor_id, actor_name=actor_name or "",
            action=action, target_id=target_id, target_name=target_name or "", details=details or "",
        ))
        session.commit()
    finally:
        session.close()
