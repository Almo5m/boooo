import datetime
from database import get_session, StudentPoints

# نقاط الإجابة الصحيحة حسب صعوبة السؤال
DIFFICULTY_POINTS = {"easy": 5, "medium": 10, "hard": 15}

# حدود الشارات - المستوى بيتفتح تلقائي مع تراكم النقاط
BADGES = [
    (0, "🌱", "مبتدئ"),
    (100, "🥉", "برونزي"),
    (300, "🥈", "فضي"),
    (600, "🥇", "دهبي"),
    (1000, "💎", "ألماس"),
    (2000, "👑", "أسطورة"),
]


def get_level(points: int) -> int:
    """كل ١٠٠ نقطة = مستوى جديد."""
    return (points // 100) + 1


def get_badge(points: int) -> tuple[str, str]:
    """بترجع (إيموجي الشارة، اسمها) حسب النقاط الحالية."""
    current = BADGES[0]
    for threshold, emoji, name in BADGES:
        if points >= threshold:
            current = (threshold, emoji, name)
    return current[1], current[2]


def next_badge_info(points: int) -> tuple[int, str, str] | None:
    """بترجع (النقاط الناقصة، إيموجي الشارة الجاية، اسمها) أو None لو وصل لأعلى شارة."""
    for threshold, emoji, name in BADGES:
        if points < threshold:
            return threshold - points, emoji, name
    return None


def award_points(chat_id: int, user_id: int, name: str, points: int) -> StudentPoints:
    """بتضيف نقاط لطالب في جروب معين، وبتنشئ سجله لو أول مرة. بترجع السجل بعد التحديث."""
    session = get_session()
    try:
        row = session.query(StudentPoints).filter_by(chat_id=chat_id, user_id=user_id).first()
        if not row:
            row = StudentPoints(chat_id=chat_id, user_id=user_id, name=name, points=0, correct_answers=0)
            session.add(row)
        row.points += points
        row.correct_answers += 1
        row.name = name or row.name
        row.updated_at = datetime.datetime.utcnow()
        session.commit()
        session.refresh(row)
        return row
    finally:
        session.close()


def get_leaderboard(chat_id: int, limit: int = 10):
    session = get_session()
    try:
        return (
            session.query(StudentPoints)
            .filter_by(chat_id=chat_id)
            .order_by(StudentPoints.points.desc())
            .limit(limit)
            .all()
        )
    finally:
        session.close()


def get_student_points(chat_id: int, user_id: int):
    session = get_session()
    try:
        return session.query(StudentPoints).filter_by(chat_id=chat_id, user_id=user_id).first()
    finally:
        session.close()
