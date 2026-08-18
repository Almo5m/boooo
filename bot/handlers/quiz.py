import json
import asyncio
from aiogram import Router
from aiogram.types import Message
from database import get_session, Exam, Question, ExamResult
from bot.handlers.arabic_commands import is_group_admin, match_command

router = Router()

ACTIVE_POLLS = {}
LIVE_SCORES = {}

START_EXAM_WORDS = {"ابدأ اختبار", "ابدء اختبار", "شغل اختبار"}
MY_RESULTS_WORDS = {"نتايجي", "نتائجي"}

QUIZ_COMMAND_WORDS = START_EXAM_WORDS | MY_RESULTS_WORDS


def is_quiz_command(message: Message) -> bool:
    if not message.text:
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
        exam_id = int(arg)
        session = get_session()
        try:
            exam = session.query(Exam).filter_by(id=exam_id).first()
            if not exam:
                await message.reply("❌ مفيش اختبار بالرقم ده.")
                return
            q_ids = json.loads(exam.question_ids)
            questions = session.query(Question).filter(Question.id.in_(q_ids)).all()
            q_map = {q.id: q for q in questions}
            ordered = [q_map[i] for i in q_ids if i in q_map]

            if not ordered:
                await message.reply("الاختبار ده فاضي من الأسئلة.")
                return

            await message.answer(f"📝 <b>بدء اختبار: {exam.title}</b>\nعدد الأسئلة: {len(ordered)}\nحظ سعيد للجميع! 🍀")

            key = (message.chat.id, exam.id)
            LIVE_SCORES[key] = {}

            for q in ordered:
                options = json.loads(q.options)
                poll = await message.bot.send_poll(
                    chat_id=message.chat.id,
                    question=q.text[:290],
                    options=[o[:100] for o in options],
                    type="quiz",
                    correct_option_id=q.correct_index,
                    is_anonymous=False,
                    open_period=exam.time_per_question,
                    explanation=(q.explanation or "")[:190],
                )
                ACTIVE_POLLS[poll.poll.id] = {
                    "chat_id": message.chat.id,
                    "exam_id": exam.id,
                    "correct_index": q.correct_index,
                }
                await asyncio.sleep(exam.time_per_question + 1)

            await message.answer("✅ خلص الاختبار! هعرض النتائج دلوقتي 👇")
            scores = LIVE_SCORES.pop(key, {})
            if not scores:
                await message.answer("محدش جاوب على أي سؤال 😅")
                return

            ranked = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
            result_text = f"🏆 <b>نتائج اختبار: {exam.title}</b>\n\n"
            for i, r in enumerate(ranked, 1):
                result_text += f"{i}. {r['name']}: {r['score']}/{len(ordered)}\n"
                session.add(ExamResult(
                    exam_id=exam.id, chat_id=message.chat.id, user_id=r["user_id"],
                    user_name=r["name"], score=r["score"], total=len(ordered),
                ))
            session.commit()
            await message.answer(result_text)
        finally:
            session.close()
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
