import json
import html
import asyncio
from aiogram import Router, Bot
from aiogram.types import Message
from database import get_session, Exam, Question, ExamResult
from bot.handlers.arabic_commands import is_group_admin, match_command
from bot.points import award_points, get_leaderboard, get_student_points, get_level, get_badge, next_badge_info, DIFFICULTY_POINTS

router = Router()

ACTIVE_POLLS = {}
LIVE_SCORES = {}
# اختبار شغال دلوقتي في كل جروب: chat_id -> {"cancel_event": asyncio.Event, "exam_title": str}
ACTIVE_EXAMS = {}

START_EXAM_WORDS = {"ابدأ اختبار", "ابدء اختبار", "شغل اختبار"}
STOP_EXAM_WORDS = {"وقف الاختبار", "ايقاف الاختبار", "إيقاف الاختبار"}
STATIC_QUESTIONS_WORDS = {"اسئلة للمراجعة", "أسئلة للمراجعة", "اسئلة اختبار", "أسئلة اختبار"}
MY_RESULTS_WORDS = {"نتايجي", "نتائجي"}
MY_POINTS_WORDS = {"نقاطي", "مستواي"}
LEADERBOARD_WORDS = {"الترتيب", "لوحة الصدارة"}

QUIZ_COMMAND_WORDS = (
    START_EXAM_WORDS | STOP_EXAM_WORDS | STATIC_QUESTIONS_WORDS
    | MY_RESULTS_WORDS | MY_POINTS_WORDS | LEADERBOARD_WORDS
)


def is_exam_active(chat_id: int) -> bool:
    return chat_id in ACTIVE_EXAMS


def stop_exam(chat_id: int) -> bool:
    """بتوقف الاختبار الجاري في الجروب ده لو موجود. بترجع True لو فعلاً كان فيه اختبار شغال."""
    info = ACTIVE_EXAMS.get(chat_id)
    if not info:
        return False
    info["cancel_event"].set()
    return True


async def run_exam(bot: Bot, chat_id: int, exam_id: int) -> str:
    """
    بتشغل الاختبار فعليًا في أي جروب - مستخدمة من الأمر العربي وكمان من لوحة تحكم الخاص.
    بترجع رسالة الحالة النهائية (نجاح أو سبب الفشل).
    """
    if is_exam_active(chat_id):
        return "⚠️ فيه اختبار شغال بالفعل في الجروب ده، وقفه الأول."

    session = get_session()
    try:
        exam = session.query(Exam).filter_by(id=exam_id).first()
        if not exam:
            return "❌ مفيش اختبار بالرقم ده."
        q_ids = json.loads(exam.question_ids)
        questions = session.query(Question).filter(Question.id.in_(q_ids)).all()
        q_map = {q.id: q for q in questions}
        ordered = [q_map[i] for i in q_ids if i in q_map]

        if not ordered:
            return "الاختبار ده فاضي من الأسئلة."

        cancel_event = asyncio.Event()
        ACTIVE_EXAMS[chat_id] = {"cancel_event": cancel_event, "exam_title": exam.title}

        await bot.send_message(chat_id, f"📝 <b>بدء اختبار: {exam.title}</b>\nعدد الأسئلة: {len(ordered)}\nحظ سعيد للجميع! 🍀")

        key = (chat_id, exam.id)
        LIVE_SCORES[key] = {}

        was_cancelled = False
        current_poll_id = None
        questions_sent = 0

        for q in ordered:
            if cancel_event.is_set():
                was_cancelled = True
                break

            options = json.loads(q.options)
            poll = await bot.send_poll(
                chat_id=chat_id,
                question=q.text[:290],
                options=[o[:100] for o in options],
                type="quiz",
                correct_option_id=q.correct_index,
                is_anonymous=False,
                open_period=exam.time_per_question,
                explanation=(q.explanation or "")[:190],
            )
            questions_sent += 1
            current_poll_id = poll.message_id
            ACTIVE_POLLS[poll.poll.id] = {
                "chat_id": chat_id,
                "exam_id": exam.id,
                "correct_index": q.correct_index,
                "difficulty": q.difficulty,
            }

            # بننتظر بمقاطع قصيرة (ثانية بثانية) عشان لو حد وقف الاختبار نلاقي بسرعة
            for _ in range(exam.time_per_question + 1):
                if cancel_event.is_set():
                    was_cancelled = True
                    break
                await asyncio.sleep(1)

            if was_cancelled:
                try:
                    await bot.stop_poll(chat_id, current_poll_id)
                except Exception:
                    pass
                break

        ACTIVE_EXAMS.pop(chat_id, None)

        if was_cancelled:
            await bot.send_message(chat_id, "⏹ تم إيقاف الاختبار. هعرض نتائج الأسئلة اللي اتجاوبت لحد دلوقتي 👇")
        else:
            await bot.send_message(chat_id, "✅ خلص الاختبار! هعرض النتائج دلوقتي 👇")

        scores = LIVE_SCORES.pop(key, {})
        if not scores:
            await bot.send_message(chat_id, "محدش جاوب على أي سؤال 😅")
            return "تم إنهاء الاختبار (محدش جاوب)."

        ranked = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
        result_text = f"🏆 <b>نتائج اختبار: {exam.title}</b>\n\n"
        for i, r in enumerate(ranked, 1):
            result_text += f"{i}. {r['name']}: {r['score']}/{questions_sent}\n"
            session.add(ExamResult(
                exam_id=exam.id, chat_id=chat_id, user_id=r["user_id"],
                user_name=r["name"], score=r["score"], total=questions_sent,
            ))
        session.commit()
        await bot.send_message(chat_id, result_text)
        return "✅ تم إيقاف الاختبار وعرض النتائج الجزئية." if was_cancelled else "✅ خلص الاختبار وتم عرض النتائج."
    finally:
        ACTIVE_EXAMS.pop(chat_id, None)
        session.close()


async def post_static_questions(bot: Bot, chat_id: int, exam_id: int) -> str:
    """
    بتنشر أسئلة الاختبار كرسائل ثابتة تفضل ظاهرة في الجروب للأبد، والإجابة
    مخفية جوه Spoiler (المستخدم بيدوس بإيده عشان يكشفها) - مناسبة للمراجعة
    الحرة بدل الاختبار المؤقت المحسوب بنقط.
    """
    session = get_session()
    try:
        exam = session.query(Exam).filter_by(id=exam_id).first()
        if not exam:
            return "❌ مفيش اختبار بالرقم ده."
        q_ids = json.loads(exam.question_ids)
        questions = session.query(Question).filter(Question.id.in_(q_ids)).all()
        q_map = {q.id: q for q in questions}
        ordered = [q_map[i] for i in q_ids if i in q_map]

        if not ordered:
            return "الاختبار ده فاضي من الأسئلة."

        await bot.send_message(
            chat_id,
            f"📄 <b>أسئلة للمراجعة: {html.escape(exam.title)}</b>\n"
            f"جاوب في دماغك الأول، وبعدين دوس على الإجابة عشان تظهر 👇",
        )

        for i, q in enumerate(ordered, 1):
            options = json.loads(q.options)
            opts_text = "\n".join(f"{idx + 1}) {html.escape(opt)}" for idx, opt in enumerate(options))
            correct = html.escape(options[q.correct_index])
            hidden_content = correct
            if q.explanation:
                hidden_content += f"\n💡 {html.escape(q.explanation)}"
            text = (
                f"❓ <b>سؤال {i}:</b> {html.escape(q.text)}\n\n"
                f"{opts_text}\n\n"
                f"الإجابة: <tg-spoiler>{hidden_content}</tg-spoiler>"
            )
            await bot.send_message(chat_id, text)
            await asyncio.sleep(1)  # تجنب حدود تليجرام لمعدل إرسال الرسائل

        return "✅ تم نشر الأسئلة للمراجعة."
    finally:
        session.close()


def is_quiz_command(message: Message) -> bool:
    if not message.text or message.chat.type == "private":
        return False
    cmd, _ = match_command(message.text, QUIZ_COMMAND_WORDS)
    return cmd is not None


@router.message(is_quiz_command)
async def quiz_router(message: Message):
    text = message.text.strip()
    cmd, rest = match_command(text, QUIZ_COMMAND_WORDS)

    if cmd in START_EXAM_WORDS:
        if not await is_group_admin(message):
            return
        arg = rest.strip()
        if not arg.isdigit():
            await message.reply("استخدم: ابدأ اختبار [رقم_الاختبار] (شوف الأرقام من لوحة التحكم)")
            return
        result = await run_exam(message.bot, message.chat.id, int(arg))
        if result.startswith("❌") or result.startswith("الاختبار") or result.startswith("⚠️"):
            await message.reply(result)
        return

    if cmd in STOP_EXAM_WORDS:
        if not await is_group_admin(message):
            return
        if stop_exam(message.chat.id):
            await message.reply("⏹ جاري إيقاف الاختبار...")
        else:
            await message.reply("مفيش اختبار شغال دلوقتي في الجروب ده.")
        return

    if cmd in STATIC_QUESTIONS_WORDS:
        if not await is_group_admin(message):
            return
        arg = rest.strip()
        if not arg.isdigit():
            await message.reply("استخدم: اسئلة للمراجعة [رقم_الاختبار] (شوف الأرقام من لوحة التحكم)")
            return
        result = await post_static_questions(message.bot, message.chat.id, int(arg))
        if result.startswith("❌") or result.startswith("الاختبار"):
            await message.reply(result)
        return

    if cmd in MY_RESULTS_WORDS:
        session = get_session()
        try:
            results = (
                session.query(ExamResult)
                .filter_by(chat_id=message.chat.id, user_id=message.from_user.id)
                .order_by(ExamResult.finished_at.desc())
                .limit(10)
                .all()
            )
            if not results:
                await message.reply("لسه مالكش نتايج اختبارات مسجلة.")
                return
            result_text = "📊 <b>آخر نتائجك:</b>\n"
            for r in results:
                result_text += f"• اختبار #{r.exam_id}: {r.score}/{r.total}\n"
            await message.reply(result_text)
        finally:
            session.close()
        return

    if cmd in MY_POINTS_WORDS:
        row = get_student_points(message.chat.id, message.from_user.id)
        if not row:
            await message.reply("لسه ماجاوبتش صح في أي اختبار عشان تكسب نقاط 🙂")
            return
        emoji, name_ar = get_badge(row.points)
        level = get_level(row.points)
        text = (
            f"{emoji} <b>{message.from_user.full_name}</b>\n"
            f"النقاط: {row.points}\n"
            f"المستوى: {level}\n"
            f"الشارة: {name_ar}\n"
            f"إجابات صحيحة: {row.correct_answers}"
        )
        nxt = next_badge_info(row.points)
        if nxt:
            remaining, next_emoji, next_name = nxt
            text += f"\n\nمتبقي {remaining} نقطة للشارة الجاية: {next_emoji} {next_name}"
        await message.reply(text)
        return

    if cmd in LEADERBOARD_WORDS:
        board = get_leaderboard(message.chat.id, limit=10)
        if not board:
            await message.reply("لسه محدش كسب نقاط في الجروب ده.")
            return
        lines = ["🏆 <b>لوحة الصدارة</b>\n"]
        medals = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(board):
            medal = medals[i] if i < 3 else f"{i + 1}."
            emoji, _ = get_badge(row.points)
            lines.append(f"{medal} {row.name} {emoji} — {row.points} نقطة")
        await message.reply("\n".join(lines))
        return


@router.poll_answer()
async def on_poll_answer(poll_answer):
    info = ACTIVE_POLLS.get(poll_answer.poll_id)
    if not info:
        return
    key = (info["chat_id"], info["exam_id"])
    scores = LIVE_SCORES.setdefault(key, {})
    user = poll_answer.user
    entry = scores.setdefault(user.id, {"name": user.full_name, "score": 0, "total": 0, "user_id": user.id})
    entry["total"] += 1
    if poll_answer.option_ids and poll_answer.option_ids[0] == info["correct_index"]:
        entry["score"] += 1
        points = DIFFICULTY_POINTS.get(info.get("difficulty"), 10)
        award_points(info["chat_id"], user.id, user.full_name, points)
