import json
import datetime
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document, ChatPermissions,
)
from aiogram.filters import Command

from database import (
    get_session, Subject, Question, Exam, ScheduledMessage, GroupSettings, Admin, ExamResult, BotUser,
    ActivityLog, Warning, log_action, FilterWord, Note,
)
from bot.handlers.arabic_commands import is_bot_admin_id
from bot.excel_import import import_questions_from_excel
from bot.handlers.quiz import run_exam, is_exam_active, stop_exam, post_static_questions
from bot.points import get_leaderboard, get_badge
from scheduler import schedule_message

router = Router()

# حالة بسيطة في الذاكرة لتتبع خطوة كل مستخدم وسط عملية بترد بنص حر (اسم مادة، محتوى رسالة...)
PENDING = {}


def kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows
    ])


def group_label(g: GroupSettings) -> str:
    return g.title if g.title else f"جروب {g.chat_id}"


def main_menu_kb() -> InlineKeyboardMarkup:
    return kb([
        [("📚 المواد", "m:subjects"), ("❓ الأسئلة", "m:questions")],
        [("📝 الاختبارات", "m:exams"), ("🗓 الجدولة", "m:schedule")],
        [("👥 المشرفين", "m:admins"), ("⚙️ إعدادات الجروبات", "m:settings")],
        [("👤 المستخدمين", "m:users"), ("📊 الإحصائيات", "m:stats")],
        [("🛡 إدارة سريعة", "m:moderation"), ("📋 سجل النشاط", "m:activity")],
        [("🏆 نقاط الطلاب", "m:leaderboard"), ("📢 إرسال فوري", "m:broadcast")],
    ])


async def show_main_menu(target):
    text = "🎛 <b>لوحة تحكم البوت</b>\nاختار من تحت:"
    if isinstance(target, Message):
        await target.answer(text, reply_markup=main_menu_kb())
    else:
        await target.message.edit_text(text, reply_markup=main_menu_kb())


@router.message(Command("start"))
async def dm_start(message: Message):
    if message.chat.type != "private":
        return
    if not is_bot_admin_id(message.from_user.id):
        await message.answer("أهلاً بيك! أنا بو 🎓 كلمني عادي أو ابعتلي أي سؤال في الجروب.")
        return
    PENDING.pop(message.from_user.id, None)
    await show_main_menu(message)


@router.message(Command("لوحة"))
async def dm_panel_cmd(message: Message):
    if message.chat.type != "private" or not is_bot_admin_id(message.from_user.id):
        return
    PENDING.pop(message.from_user.id, None)
    await show_main_menu(message)


def _admin_only_callback(func):
    async def wrapper(callback: CallbackQuery):
        if not is_bot_admin_id(callback.from_user.id):
            await callback.answer("مش من صلاحياتك 🙏", show_alert=True)
            return
        await func(callback)
    wrapper.__name__ = func.__name__
    return wrapper


@router.callback_query(F.data == "m:main")
@_admin_only_callback
async def cb_main(callback: CallbackQuery):
    PENDING.pop(callback.from_user.id, None)
    await show_main_menu(callback)
    await callback.answer()


# ---------------- المواد ----------------

@router.callback_query(F.data == "m:subjects")
@_admin_only_callback
async def cb_subjects(callback: CallbackQuery):
    session = get_session()
    try:
        subjects = session.query(Subject).all()
        rows = [[("➕ إضافة مادة", "m:add_subject")]]
        for s in subjects:
            rows.append([(f"{s.name} ({len(s.questions)})", "m:noop"), ("🗑", f"m:del_subject:{s.id}")])
        rows.append([("« رجوع", "m:main")])
        text = "📚 <b>المواد:</b>" if subjects else "📚 <b>المواد:</b>\n\nمفيش مواد بعد."
        await callback.message.edit_text(text, reply_markup=kb(rows))
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data == "m:noop")
async def cb_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("m:del_subject:"))
@_admin_only_callback
async def cb_del_subject(callback: CallbackQuery):
    subject_id = int(callback.data.split(":")[2])
    session = get_session()
    try:
        s = session.query(Subject).filter_by(id=subject_id).first()
        if s:
            session.delete(s)
            session.commit()
    finally:
        session.close()
    await callback.answer("تم الحذف ✅ (والأسئلة اللي جواها)")
    await cb_subjects(callback)


@router.callback_query(F.data == "m:add_subject")
@_admin_only_callback
async def cb_add_subject(callback: CallbackQuery):
    PENDING[callback.from_user.id] = {"action": "add_subject"}
    await callback.message.edit_text(
        "اكتب اسم المادة اللي عايز تضيفها:",
        reply_markup=kb([[("« إلغاء", "m:subjects")]]),
    )
    await callback.answer()


# ---------------- الأسئلة ----------------

@router.callback_query(F.data == "m:questions")
@_admin_only_callback
async def cb_questions(callback: CallbackQuery):
    session = get_session()
    try:
        subjects = session.query(Subject).all()
        if not subjects:
            await callback.message.edit_text(
                "لازم تضيف مادة الأول قبل ما تضيف أسئلة.",
                reply_markup=kb([[("➕ إضافة مادة", "m:add_subject")], [("« رجوع", "m:main")]]),
            )
            await callback.answer()
            return
        rows = [[(f"{s.name} ({len(s.questions)})", f"m:subj_questions:{s.id}")] for s in subjects]
        rows.append([("« رجوع", "m:main")])
        await callback.message.edit_text("❓ <b>بنك الأسئلة</b>\nاختار المادة:", reply_markup=kb(rows))
    finally:
        session.close()
    await callback.answer()


async def render_subject_questions(callback: CallbackQuery, subject_id: int):
    session = get_session()
    try:
        subj = session.query(Subject).filter_by(id=subject_id).first()
        if not subj:
            await callback.message.edit_text("المادة مش موجودة.", reply_markup=kb([[("« رجوع", "m:questions")]]))
            return
        rows = [
            [("📤 رفع إكسيل", f"m:upload:{subject_id}"), ("➕ سؤال يدوي", f"m:new_question:{subject_id}")],
        ]
        for q in subj.questions[:30]:
            rows.append([(q.text[:35], "m:noop"), ("✏️", f"m:q_edit:{q.id}"), ("🗑", f"m:q_del:{q.id}")])
        rows.append([("« رجوع", "m:questions")])
        text = f"❓ <b>أسئلة مادة {subj.name}</b> ({len(subj.questions)})"
        if len(subj.questions) > 30:
            text += "\n(عرض أول ٣٠ سؤال بس)"
        await callback.message.edit_text(text, reply_markup=kb(rows))
    finally:
        session.close()


@router.callback_query(F.data.startswith("m:subj_questions:"))
@_admin_only_callback
async def cb_subj_questions(callback: CallbackQuery):
    subject_id = int(callback.data.split(":")[2])
    await render_subject_questions(callback, subject_id)
    await callback.answer()


@router.callback_query(F.data.startswith("m:q_del:"))
@_admin_only_callback
async def cb_q_del(callback: CallbackQuery):
    question_id = int(callback.data.split(":")[2])
    session = get_session()
    try:
        q = session.query(Question).filter_by(id=question_id).first()
        subject_id = q.subject_id if q else None
        if q:
            session.delete(q)
            session.commit()
    finally:
        session.close()
    await callback.answer("تم الحذف ✅")
    if subject_id:
        await render_subject_questions(callback, subject_id)
    else:
        await cb_questions(callback)


@router.callback_query(F.data.startswith("m:upload:"))
@_admin_only_callback
async def cb_upload(callback: CallbackQuery):
    subject_id = int(callback.data.split(":")[2])
    PENDING[callback.from_user.id] = {"action": "upload_questions", "subject_id": subject_id}
    await callback.message.edit_text(
        "📎 ابعت ملف إكسيل (.xlsx) بالأسئلة دلوقتي.\n\n"
        "ترتيب الأعمدة:\n"
        "A: نص السؤال | B,C,D,E: الاختيارات (لغاية 4) | F: رقم الإجابة الصحيحة (0=الأول) | G: شرح (اختياري) | H: الصعوبة (اختياري)",
        reply_markup=kb([[("« إلغاء", f"m:subj_questions:{subject_id}")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("m:new_question:"))
@_admin_only_callback
async def cb_new_question(callback: CallbackQuery):
    subject_id = int(callback.data.split(":")[2])
    PENDING[callback.from_user.id] = {"action": "q_text", "mode": "new", "subject_id": subject_id}
    await callback.message.edit_text(
        "اكتب نص السؤال:",
        reply_markup=kb([[("« إلغاء", f"m:subj_questions:{subject_id}")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("m:q_edit:"))
@_admin_only_callback
async def cb_q_edit(callback: CallbackQuery):
    question_id = int(callback.data.split(":")[2])
    session = get_session()
    try:
        q = session.query(Question).filter_by(id=question_id).first()
        if not q:
            await callback.answer("السؤال مش موجود.", show_alert=True)
            return
        PENDING[callback.from_user.id] = {"action": "q_text", "mode": "edit", "question_id": question_id, "subject_id": q.subject_id}
        await callback.message.edit_text(
            f"النص الحالي: {q.text}\n\nاكتب نص السؤال الجديد:",
            reply_markup=kb([[("« إلغاء", f"m:subj_questions:{q.subject_id}")]]),
        )
    finally:
        session.close()
    await callback.answer()


def has_pending_upload(message: Message) -> bool:
    if message.chat.type != "private" or not message.document:
        return False
    pending = PENDING.get(message.from_user.id)
    return bool(pending) and pending.get("action") == "upload_questions" and is_bot_admin_id(message.from_user.id)


@router.message(has_pending_upload)
async def handle_document_upload(message: Message):
    pending = PENDING.get(message.from_user.id)
    doc: Document = message.document
    if not doc.file_name.endswith(".xlsx"):
        await message.reply("لازم يكون الملف بصيغة .xlsx")
        return

    await message.reply("⏳ بحمّل الأسئلة...")
    file = await message.bot.get_file(doc.file_id)
    file_bytes = await message.bot.download_file(file.file_path)
    added, errors = import_questions_from_excel(file_bytes.read(), pending["subject_id"])

    PENDING.pop(message.from_user.id, None)
    result = f"✅ تم إضافة {added} سؤال بنجاح."
    if errors:
        result += f"\n\n⚠️ اتجاهلت {len(errors)} صف فيهم مشكلة:\n" + "\n".join(errors[:10])
    await message.answer(result, reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))


# ---------------- الاختبارات ----------------

@router.callback_query(F.data == "m:exams")
@_admin_only_callback
async def cb_exams(callback: CallbackQuery):
    session = get_session()
    try:
        exams = session.query(Exam).order_by(Exam.id.desc()).all()
        groups = session.query(GroupSettings).all()
        active_groups = [g for g in groups if is_exam_active(g.chat_id)]

        rows = []
        if active_groups:
            for g in active_groups:
                rows.append([(f"⏹ إيقاف الاختبار في {group_label(g)}", f"m:stopexam:{g.chat_id}")])

        rows.append([("➕ اختبار جديد", "m:new_exam")])

        if not exams:
            rows.append([("« رجوع", "m:main")])
            text = "📝 <b>الاختبارات</b>\n\nمفيش اختبارات لسه، اعمل واحد من الزرار فوق."
            if active_groups:
                text = "⏹ فيه اختبار شغال دلوقتي، تقدر توقفه من تحت.\n\n" + text
            await callback.message.edit_text(text, reply_markup=kb(rows))
            await callback.answer()
            return

        for e in exams:
            rows.append([
                (f"▶️ {e.title}", f"m:startexam:{e.id}"),
                ("📄 مراجعة", f"m:staticexam:{e.id}"),
                ("✏️", f"m:exam_edit:{e.id}"),
                ("🗑", f"m:exam_del:{e.id}"),
            ])
        rows.append([("« رجوع", "m:main")])
        await callback.message.edit_text(
            "📝 <b>الاختبارات المتاحة:</b>\n"
            "▶️ يشغّل اختبار مؤقت بنقط ومنافسة\n"
            "📄 ينشر الأسئلة كمراجعة دايمة والإجابة مخفية جوه Spoiler",
            reply_markup=kb(rows),
        )
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data.startswith("m:stopexam:"))
@_admin_only_callback
async def cb_stop_exam(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    stopped = stop_exam(chat_id)
    await callback.answer("⏹ جاري الإيقاف..." if stopped else "مفيش اختبار شغال دلوقتي.", show_alert=True)
    await cb_exams(callback)


@router.callback_query(F.data.startswith("m:startexam:"))
@_admin_only_callback
async def cb_start_exam_pick_group(callback: CallbackQuery):
    exam_id = int(callback.data.split(":")[2])
    session = get_session()
    try:
        groups = session.query(GroupSettings).all()
        if not groups:
            await callback.message.edit_text(
                "لسه مفيش جروب مسجل. لازم البوت يستخدم فيه أمر أول مرة عشان يتسجل.",
                reply_markup=kb([[("« رجوع", "m:exams")]]),
            )
            await callback.answer()
            return
        rows = [[(group_label(g), f"m:startexam_go:{exam_id}:{g.chat_id}")] for g in groups]
        rows.append([("« رجوع", "m:exams")])
        await callback.message.edit_text("اختار الجروب اللي هيتشغل فيه الاختبار:", reply_markup=kb(rows))
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data.startswith("m:startexam_go:"))
@_admin_only_callback
async def cb_start_exam_go(callback: CallbackQuery):
    _, _, exam_id, chat_id = callback.data.split(":")
    await callback.message.edit_text("🚀 جاري تشغيل الاختبار في الجروب...")
    await callback.answer()
    result = await run_exam(callback.bot, int(chat_id), int(exam_id))
    await callback.message.answer(result, reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))


@router.callback_query(F.data.startswith("m:staticexam:"))
@_admin_only_callback
async def cb_static_exam_pick_group(callback: CallbackQuery):
    exam_id = int(callback.data.split(":")[2])
    session = get_session()
    try:
        groups = session.query(GroupSettings).all()
        if not groups:
            await callback.message.edit_text(
                "لسه مفيش جروب مسجل. لازم البوت يستخدم فيه أمر أول مرة عشان يتسجل.",
                reply_markup=kb([[("« رجوع", "m:exams")]]),
            )
            await callback.answer()
            return
        rows = [[(group_label(g), f"m:staticexam_go:{exam_id}:{g.chat_id}")] for g in groups]
        rows.append([("« رجوع", "m:exams")])
        await callback.message.edit_text("اختار الجروب اللي هتنشر فيه الأسئلة للمراجعة:", reply_markup=kb(rows))
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data.startswith("m:staticexam_go:"))
@_admin_only_callback
async def cb_static_exam_go(callback: CallbackQuery):
    _, _, exam_id, chat_id = callback.data.split(":")
    await callback.message.edit_text("📤 جاري نشر الأسئلة...")
    await callback.answer()
    result = await post_static_questions(callback.bot, int(chat_id), int(exam_id))
    await callback.message.answer(result, reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))


@router.callback_query(F.data == "m:new_exam")
@_admin_only_callback
async def cb_new_exam(callback: CallbackQuery):
    session = get_session()
    try:
        subjects = session.query(Subject).all()
        rows = [[(s.name, f"m:new_exam_subj:{s.id}")] for s in subjects]
        rows.append([("🌐 كل المواد", "m:new_exam_subj:all")])
        rows.append([("« إلغاء", "m:exams")])
        await callback.message.edit_text(
            "اختار المادة عشان تختار منها الأسئلة (أو كل المواد):", reply_markup=kb(rows)
        )
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data.startswith("m:new_exam_subj:"))
@_admin_only_callback
async def cb_new_exam_subj(callback: CallbackQuery):
    raw = callback.data.split(":")[2]
    subject_id = None if raw == "all" else int(raw)
    PENDING[callback.from_user.id] = {"action": "new_exam_title", "subject_id": subject_id}
    await callback.message.edit_text("اكتب عنوان الاختبار:", reply_markup=kb([[("« إلغاء", "m:exams")]]))
    await callback.answer()


@router.callback_query(F.data.startswith("m:exam_edit:"))
@_admin_only_callback
async def cb_exam_edit(callback: CallbackQuery):
    exam_id = int(callback.data.split(":")[2])
    session = get_session()
    try:
        exam = session.query(Exam).filter_by(id=exam_id).first()
        if not exam:
            await callback.answer("الاختبار مش موجود.", show_alert=True)
            return
        PENDING[callback.from_user.id] = {
            "action": "edit_exam_title",
            "exam_id": exam_id,
            "title": exam.title,
            "time": exam.time_per_question,
            "subject_id": exam.subject_id,
            "selected": set(json.loads(exam.question_ids)),
        }
        await callback.message.edit_text(
            f"العنوان الحالي: {exam.title}\n\nاكتب عنوان جديد، أو ابعت - عشان تسيبه زي ما هو:",
            reply_markup=kb([[("« إلغاء", "m:exams")]]),
        )
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data.startswith("m:exam_del:"))
@_admin_only_callback
async def cb_exam_del(callback: CallbackQuery):
    exam_id = int(callback.data.split(":")[2])
    session = get_session()
    try:
        exam = session.query(Exam).filter_by(id=exam_id).first()
        if exam:
            session.delete(exam)
            session.commit()
    finally:
        session.close()
    await callback.answer("تم الحذف ✅")
    await cb_exams(callback)


def render_exam_picker(user_id: int):
    """بترجع (النص، الكيبورد) لقائمة اختيار الأسئلة وقت بناء/تعديل اختبار."""
    pending = PENDING[user_id]
    session = get_session()
    try:
        subject_id = pending.get("subject_id")
        q_query = session.query(Question)
        if subject_id:
            q_query = q_query.filter_by(subject_id=subject_id)
        questions = q_query.order_by(Question.id.desc()).limit(40).all()
        rows = []
        for q in questions:
            checked = "✅" if q.id in pending["selected"] else "⬜"
            rows.append([(f"{checked} {q.text[:40]}", f"m:examq_toggle:{q.id}")])
        rows.append([(f"💾 حفظ ({len(pending['selected'])} سؤال)", "m:examq_save")])
        rows.append([("« إلغاء", "m:exams")])
        text = (
            f"اختار الأسئلة (دوس تحدد/تشيل):\n"
            f"العنوان: {pending['title']} — الوقت لكل سؤال: {pending['time']} ثانية"
        )
        return text, kb(rows)
    finally:
        session.close()


@router.callback_query(F.data.startswith("m:examq_toggle:"))
@_admin_only_callback
async def cb_examq_toggle(callback: CallbackQuery):
    question_id = int(callback.data.split(":")[2])
    pending = PENDING.get(callback.from_user.id)
    if not pending or pending.get("action") != "exam_picker":
        await callback.answer("انتهت الجلسة، ابدأ تاني.", show_alert=True)
        return
    if question_id in pending["selected"]:
        pending["selected"].remove(question_id)
    else:
        pending["selected"].add(question_id)
    text, markup = render_exam_picker(callback.from_user.id)
    await callback.message.edit_text(text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "m:examq_save")
@_admin_only_callback
async def cb_examq_save(callback: CallbackQuery):
    pending = PENDING.get(callback.from_user.id)
    if not pending or pending.get("action") != "exam_picker":
        await callback.answer("انتهت الجلسة، ابدأ تاني.", show_alert=True)
        return
    if not pending["selected"]:
        await callback.answer("لازم تختار سؤال واحد على الأقل.", show_alert=True)
        return
    q_ids = list(pending["selected"])
    session = get_session()
    try:
        if pending["mode"] == "new":
            session.add(Exam(
                title=pending["title"], subject_id=pending.get("subject_id"),
                question_ids=json.dumps(q_ids), time_per_question=pending["time"],
            ))
        else:
            exam = session.query(Exam).filter_by(id=pending["exam_id"]).first()
            if exam:
                exam.title = pending["title"]
                exam.question_ids = json.dumps(q_ids)
                exam.time_per_question = pending["time"]
        session.commit()
    finally:
        session.close()
    PENDING.pop(callback.from_user.id, None)
    await callback.message.edit_text(
        f"✅ تم حفظ الاختبار ({len(q_ids)} سؤال).",
        reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]),
    )
    await callback.answer()


# ---------------- الجدولة ----------------

@router.callback_query(F.data == "m:schedule")
@_admin_only_callback
async def cb_schedule(callback: CallbackQuery):
    session = get_session()
    try:
        items = session.query(ScheduledMessage).filter_by(is_active=True).order_by(ScheduledMessage.id.desc()).limit(10).all()
        text = "🗓 <b>الرسائل المجدولة النشطة:</b>"
        if not items:
            text += "\n\nمفيش رسائل مجدولة حاليًا."
        rows = [[("➕ رسالة جديدة", "m:add_schedule")]]
        for it in items:
            when = it.cron_expr or str(it.run_at)
            rows.append([(f"#{it.id} — {when} — {it.content[:25]}", "m:noop"), ("🗑", f"m:sched_del:{it.id}")])
        rows.append([("« رجوع", "m:main")])
        await callback.message.edit_text(text, reply_markup=kb(rows))
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data == "m:add_schedule")
@_admin_only_callback
async def cb_add_schedule(callback: CallbackQuery):
    session = get_session()
    try:
        groups = session.query(GroupSettings).all()
        if not groups:
            await callback.message.edit_text(
                "لسه مفيش جروب مسجل. لازم البوت يستخدم فيه أمر أول مرة عشان يتسجل.",
                reply_markup=kb([[("« رجوع", "m:schedule")]]),
            )
            await callback.answer()
            return
        rows = [[(group_label(g), f"m:sched_group:{g.chat_id}")] for g in groups]
        rows.append([("« إلغاء", "m:schedule")])
        await callback.message.edit_text("لأي جروب؟", reply_markup=kb(rows))
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data.startswith("m:sched_group:"))
@_admin_only_callback
async def cb_sched_group(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    PENDING[callback.from_user.id] = {"action": "schedule_content", "chat_id": chat_id}
    await callback.message.edit_text(
        "اكتب محتوى الرسالة اللي عايز تجدولها:",
        reply_markup=kb([[("« إلغاء", "m:schedule")]]),
    )
    await callback.answer()


TIMING_OPTIONS = [
    ("بعد ١٠ دقايق", 10), ("بعد ساعة", 60), ("بعد ٦ ساعات", 360), ("بكرة الميعاد ده", 1440),
]


@router.callback_query(F.data.startswith("m:sched_when:"))
@_admin_only_callback
async def cb_sched_when(callback: CallbackQuery):
    minutes = int(callback.data.split(":")[2])
    pending = PENDING.get(callback.from_user.id)
    if not pending or pending.get("action") != "schedule_confirm":
        await callback.answer("انتهت الجلسة، ابدأ تاني.", show_alert=True)
        return

    run_at = datetime.datetime.utcnow() + datetime.timedelta(minutes=minutes)
    session = get_session()
    try:
        msg = ScheduledMessage(chat_id=pending["chat_id"], content=pending["content"], run_at=run_at, is_active=True)
        session.add(msg)
        session.commit()
        schedule_message(callback.bot, msg)
    finally:
        session.close()

    PENDING.pop(callback.from_user.id, None)
    await callback.message.edit_text(
        f"✅ اتجدولت الرسالة، هتتبعت بعد {minutes} دقيقة.",
        reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("m:sched_del:"))
@_admin_only_callback
async def cb_sched_del(callback: CallbackQuery):
    msg_id = int(callback.data.split(":")[2])
    session = get_session()
    try:
        m = session.query(ScheduledMessage).filter_by(id=msg_id).first()
        if m:
            m.is_active = False
            session.commit()
    finally:
        session.close()
    await callback.answer("تم الإلغاء ✅")
    await cb_schedule(callback)


# ---------------- إرسال فوري (Broadcast) ----------------

@router.callback_query(F.data == "m:broadcast")
@_admin_only_callback
async def cb_broadcast(callback: CallbackQuery):
    session = get_session()
    try:
        groups = session.query(GroupSettings).all()
        if not groups:
            await callback.message.edit_text(
                "لسه مفيش جروب مسجل.",
                reply_markup=kb([[("« رجوع", "m:main")]]),
            )
            await callback.answer()
            return
        rows = [[(group_label(g), f"m:broadcast_group:{g.chat_id}")] for g in groups]
        rows.append([("« رجوع", "m:main")])
        await callback.message.edit_text("📢 ابعت فورًا لأي جروب؟", reply_markup=kb(rows))
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data.startswith("m:broadcast_group:"))
@_admin_only_callback
async def cb_broadcast_group(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    PENDING[callback.from_user.id] = {"action": "broadcast_content", "chat_id": chat_id}
    await callback.message.edit_text(
        "اكتب محتوى الرسالة اللي عايز تبعتها فورًا:",
        reply_markup=kb([[("« إلغاء", "m:broadcast")]]),
    )
    await callback.answer()


# ---------------- المشرفين ----------------

@router.callback_query(F.data == "m:admins")
@_admin_only_callback
async def cb_admins(callback: CallbackQuery):
    from bot.bot import OWNER_IDS
    session = get_session()
    try:
        admins = session.query(Admin).all()
        lines = ["👥 <b>المشرفين الحاليين:</b>\n"]
        lines.append(f"👑 المالك: {', '.join(str(x) for x in OWNER_IDS)}")
        for a in admins:
            lines.append(f"• {a.name or a.user_id} (ID: {a.user_id})")
        rows = [[("➕ إضافة مشرف", "m:add_admin")]]
        for a in admins:
            rows.append([(f"🗑 حذف {a.name or a.user_id}", f"m:del_admin:{a.user_id}")])
        rows.append([("« رجوع", "m:main")])
        await callback.message.edit_text("\n".join(lines), reply_markup=kb(rows))
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data == "m:add_admin")
@_admin_only_callback
async def cb_add_admin(callback: CallbackQuery):
    PENDING[callback.from_user.id] = {"action": "add_admin"}
    await callback.message.edit_text(
        "ابعتلي فوروارد (Forward) لأي رسالة من الشخص اللي عايز تضيفه كمشرف،\n"
        "أو ابعت آيدي التليجرام بتاعه مباشرة (رقم) لو عارفه.",
        reply_markup=kb([[("« إلغاء", "m:admins")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("m:del_admin:"))
@_admin_only_callback
async def cb_del_admin(callback: CallbackQuery):
    user_id = int(callback.data.split(":")[2])
    session = get_session()
    try:
        session.query(Admin).filter_by(user_id=user_id).delete()
        session.commit()
    finally:
        session.close()
    await callback.answer("تم الحذف ✅")
    await cb_admins(callback)


# ---------------- إعدادات الجروبات ----------------

@router.callback_query(F.data == "m:settings")
@_admin_only_callback
async def cb_settings(callback: CallbackQuery):
    session = get_session()
    try:
        groups = session.query(GroupSettings).all()
        if not groups:
            await callback.message.edit_text(
                "لسه مفيش جروب مسجل.",
                reply_markup=kb([[("« رجوع", "m:main")]]),
            )
            await callback.answer()
            return
        rows = [[(group_label(g), f"m:group_settings:{g.chat_id}")] for g in groups]
        rows.append([("« رجوع", "m:main")])
        await callback.message.edit_text("⚙️ اختار الجروب:", reply_markup=kb(rows))
    finally:
        session.close()
    await callback.answer()


async def render_group_settings(callback: CallbackQuery, chat_id: int):
    session = get_session()
    try:
        g = session.query(GroupSettings).filter_by(chat_id=chat_id).first()
        if not g:
            await callback.message.edit_text("الجروب مش موجود.", reply_markup=kb([[("« رجوع", "m:settings")]]))
            return
        text = (
            f"⚙️ <b>إعدادات {group_label(g)}</b>\n\n"
            f"📜 القوانين:\n{g.rules_text[:200]}\n\n"
            f"👋 الترحيب:\n{g.welcome_text[:200]}\n\n"
            f"⚠️ حد التحذيرات: {g.max_warnings}"
        )
        on, off = "✅", "⬜"
        rows = [
            [("✏️ تعديل القوانين", f"m:edit_rules:{chat_id}")],
            [("✏️ تعديل الترحيب", f"m:edit_welcome:{chat_id}")],
            [(f"{on if g.welcome_enabled else off} الترحيب مفعّل", f"m:toggle:welcome_enabled:{chat_id}")],
            [(f"{on if g.lock_links else off} قفل الروابط", f"m:toggle:lock_links:{chat_id}")],
            [(f"{on if g.lock_forward else off} قفل التوجيه", f"m:toggle:lock_forward:{chat_id}")],
            [(f"{on if g.lock_stickers else off} قفل الاستيكرات", f"m:toggle:lock_stickers:{chat_id}")],
            [("🚫 الكلمات الممنوعة", f"m:filters:{chat_id}"), ("🗒 الملاحظات", f"m:notes:{chat_id}")],
            [("⚠️ تحذيرات الطلاب", f"m:warnings:{chat_id}")],
            [("« رجوع", "m:settings")],
        ]
        await callback.message.edit_text(text, reply_markup=kb(rows))
    finally:
        session.close()


@router.callback_query(F.data.startswith("m:group_settings:"))
@_admin_only_callback
async def cb_group_settings(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    await render_group_settings(callback, chat_id)
    await callback.answer()


@router.callback_query(F.data.startswith("m:toggle:"))
@_admin_only_callback
async def cb_toggle_setting(callback: CallbackQuery):
    _, _, field, chat_id = callback.data.split(":")
    chat_id = int(chat_id)
    session = get_session()
    try:
        g = session.query(GroupSettings).filter_by(chat_id=chat_id).first()
        if g:
            setattr(g, field, not getattr(g, field))
            session.commit()
            log_action(chat_id, callback.from_user.id, callback.from_user.full_name, "lock" if getattr(g, field) else "unlock", details=field)
    finally:
        session.close()
    await render_group_settings(callback, chat_id)
    await callback.answer()


@router.callback_query(F.data.startswith("m:edit_rules:"))
@_admin_only_callback
async def cb_edit_rules(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    PENDING[callback.from_user.id] = {"action": "edit_rules", "chat_id": chat_id}
    await callback.message.edit_text("اكتب نص القوانين الجديد:", reply_markup=kb([[("« إلغاء", f"m:group_settings:{chat_id}")]]))
    await callback.answer()


@router.callback_query(F.data.startswith("m:edit_welcome:"))
@_admin_only_callback
async def cb_edit_welcome(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    PENDING[callback.from_user.id] = {"action": "edit_welcome", "chat_id": chat_id}
    await callback.message.edit_text(
        "اكتب رسالة الترحيب الجديدة (استخدم {name} لاسم العضو):",
        reply_markup=kb([[("« إلغاء", f"m:group_settings:{chat_id}")]]),
    )
    await callback.answer()


# ---------------- الكلمات الممنوعة ----------------

async def render_filters(callback: CallbackQuery, chat_id: int):
    session = get_session()
    try:
        items = session.query(FilterWord).filter_by(chat_id=chat_id).all()
        rows = [[("➕ إضافة", f"m:filter_add:{chat_id}")]]
        for f in items:
            rows.append([(f.trigger, "m:noop"), ("🗑", f"m:filter_del:{f.id}:{chat_id}")])
        rows.append([("« رجوع", f"m:group_settings:{chat_id}")])
        text = "🚫 <b>الكلمات الممنوعة</b>" if items else "🚫 <b>الكلمات الممنوعة</b>\n\nمفيش كلمات ممنوعة لسه."
        await callback.message.edit_text(text, reply_markup=kb(rows))
    finally:
        session.close()


@router.callback_query(F.data.startswith("m:filters:"))
@_admin_only_callback
async def cb_filters(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    await render_filters(callback, chat_id)
    await callback.answer()


@router.callback_query(F.data.startswith("m:filter_add:"))
@_admin_only_callback
async def cb_filter_add(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    PENDING[callback.from_user.id] = {"action": "add_filter_word", "chat_id": chat_id}
    await callback.message.edit_text(
        "اكتب الكلمة والرد (اختياري) بالشكل ده:\nالكلمة | الرد\n\nمثال:\nسبام | ممنوع السبام هنا",
        reply_markup=kb([[("« إلغاء", f"m:filters:{chat_id}")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("m:filter_del:"))
@_admin_only_callback
async def cb_filter_del(callback: CallbackQuery):
    _, _, filter_id, chat_id = callback.data.split(":")
    session = get_session()
    try:
        f = session.query(FilterWord).filter_by(id=int(filter_id)).first()
        if f:
            session.delete(f)
            session.commit()
            log_action(int(chat_id), callback.from_user.id, callback.from_user.full_name, "filter_del", details="من لوحة الخاص")
    finally:
        session.close()
    await callback.answer("تم الحذف ✅")
    await render_filters(callback, int(chat_id))


# ---------------- الملاحظات ----------------

async def render_notes(callback: CallbackQuery, chat_id: int):
    session = get_session()
    try:
        items = session.query(Note).filter_by(chat_id=chat_id).all()
        rows = [[("➕ إضافة", f"m:note_add:{chat_id}")]]
        for n in items:
            rows.append([(f"#{n.keyword}", "m:noop"), ("🗑", f"m:note_del:{n.id}:{chat_id}")])
        rows.append([("« رجوع", f"m:group_settings:{chat_id}")])
        text = "🗒 <b>الملاحظات المحفوظة</b>" if items else "🗒 <b>الملاحظات المحفوظة</b>\n\nمفيش ملاحظات لسه."
        await callback.message.edit_text(text, reply_markup=kb(rows))
    finally:
        session.close()


@router.callback_query(F.data.startswith("m:notes:"))
@_admin_only_callback
async def cb_notes_list(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    await render_notes(callback, chat_id)
    await callback.answer()


@router.callback_query(F.data.startswith("m:note_add:"))
@_admin_only_callback
async def cb_note_add(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    PENDING[callback.from_user.id] = {"action": "add_note", "chat_id": chat_id}
    await callback.message.edit_text(
        "اكتب الكلمة المفتاحية والمحتوى بالشكل ده:\nالكلمة | المحتوى\n\nمثال:\nمواعيد | الاختبار يوم الخميس الساعة ٦",
        reply_markup=kb([[("« إلغاء", f"m:notes:{chat_id}")]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("m:note_del:"))
@_admin_only_callback
async def cb_note_del(callback: CallbackQuery):
    _, _, note_id, chat_id = callback.data.split(":")
    session = get_session()
    try:
        n = session.query(Note).filter_by(id=int(note_id)).first()
        if n:
            session.delete(n)
            session.commit()
            log_action(int(chat_id), callback.from_user.id, callback.from_user.full_name, "note_del", details="من لوحة الخاص")
    finally:
        session.close()
    await callback.answer("تم الحذف ✅")
    await render_notes(callback, int(chat_id))


# ---------------- تحذيرات الطلاب ----------------

async def render_warnings(callback: CallbackQuery, chat_id: int):
    session = get_session()
    try:
        rows_data = session.query(Warning.user_id).filter_by(chat_id=chat_id).all()
        counts = {}
        for (user_id,) in rows_data:
            counts[user_id] = counts.get(user_id, 0) + 1
        rows = []
        for user_id, count in counts.items():
            rows.append([(f"{user_id} ({count} تحذير)", "m:noop"), ("🧹 مسح", f"m:warn_reset:{chat_id}:{user_id}")])
        rows.append([("« رجوع", f"m:group_settings:{chat_id}")])
        text = "⚠️ <b>تحذيرات الطلاب</b>" if counts else "⚠️ <b>تحذيرات الطلاب</b>\n\nمحدش عنده تحذيرات دلوقتي 🎉"
        await callback.message.edit_text(text, reply_markup=kb(rows))
    finally:
        session.close()


@router.callback_query(F.data.startswith("m:warnings:"))
@_admin_only_callback
async def cb_warnings(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    await render_warnings(callback, chat_id)
    await callback.answer()


@router.callback_query(F.data.startswith("m:warn_reset:"))
@_admin_only_callback
async def cb_warn_reset(callback: CallbackQuery):
    _, _, chat_id, user_id = callback.data.split(":")
    session = get_session()
    try:
        session.query(Warning).filter_by(chat_id=int(chat_id), user_id=int(user_id)).delete()
        session.commit()
        log_action(int(chat_id), callback.from_user.id, callback.from_user.full_name, "reset_warns", int(user_id), details="من لوحة الخاص")
    finally:
        session.close()
    await callback.answer("تم المسح ✅")
    await render_warnings(callback, int(chat_id))


# ---------------- الإحصائيات ----------------

@router.callback_query(F.data == "m:stats")
@_admin_only_callback
async def cb_stats(callback: CallbackQuery):
    session = get_session()
    try:
        text = (
            "📊 <b>إحصائيات سريعة</b>\n\n"
            f"📚 المواد: {session.query(Subject).count()}\n"
            f"❓ الأسئلة: {session.query(Question).count()}\n"
            f"📝 الاختبارات: {session.query(Exam).count()}\n"
            f"🏆 النتائج المسجلة: {session.query(ExamResult).count()}\n"
            f"🗓 الرسائل المجدولة النشطة: {session.query(ScheduledMessage).filter_by(is_active=True).count()}\n"
            f"👥 الجروبات المسجلة: {session.query(GroupSettings).count()}"
        )
        await callback.message.edit_text(text, reply_markup=kb([[("« رجوع", "m:main")]]))
    finally:
        session.close()
    await callback.answer()


# ---------------- المستخدمين ----------------

@router.callback_query(F.data == "m:users")
@_admin_only_callback
async def cb_users(callback: CallbackQuery):
    session = get_session()
    try:
        total = session.query(BotUser).count()
        recent = session.query(BotUser).order_by(BotUser.last_seen.desc()).limit(20).all()
        lines = [f"👤 <b>إجمالي المستخدمين اللي جربوا البوت: {total}</b>\n"]
        lines.append("آخر ٢٠ نشاط:")
        for u in recent:
            uname = f"@{u.username}" if u.username else ""
            lines.append(f"• {u.name} {uname} — {u.message_count} رسالة".strip())
        if not recent:
            lines.append("لسه محدش استخدم البوت.")
        await callback.message.edit_text("\n".join(lines), reply_markup=kb([[("« رجوع", "m:main")]]))
    finally:
        session.close()
    await callback.answer()


# ---------------- نقاط الطلاب ----------------

@router.callback_query(F.data == "m:leaderboard")
@_admin_only_callback
async def cb_leaderboard_pick_group(callback: CallbackQuery):
    session = get_session()
    try:
        groups = session.query(GroupSettings).all()
        if not groups:
            await callback.message.edit_text(
                "لسه مفيش جروب مسجل.",
                reply_markup=kb([[("« رجوع", "m:main")]]),
            )
            await callback.answer()
            return
        rows = [[(group_label(g), f"m:leaderboard_show:{g.chat_id}")] for g in groups]
        rows.append([("« رجوع", "m:main")])
        await callback.message.edit_text("🏆 اختار الجروب:", reply_markup=kb(rows))
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data.startswith("m:leaderboard_show:"))
@_admin_only_callback
async def cb_leaderboard_show(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    board = get_leaderboard(chat_id, limit=15)
    lines = ["🏆 <b>لوحة الصدارة</b>\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(board):
        medal = medals[i] if i < 3 else f"{i + 1}."
        emoji, _ = get_badge(row.points)
        lines.append(f"{medal} {row.name} {emoji} — {row.points} نقطة")
    if not board:
        lines.append("لسه محدش كسب نقاط في الجروب ده.")
    await callback.message.edit_text("\n".join(lines), reply_markup=kb([[("« رجوع", "m:leaderboard")]]))
    await callback.answer()


# ---------------- سجل النشاط ----------------

ACTION_LABELS = {
    "ban": "🚫 حظر", "unban": "✅ رفع حظر", "mute": "🔇 كتم", "unmute": "🔊 فك كتم",
    "kick": "👢 طرد", "warn": "⚠️ تحذير", "reset_warns": "🧹 مسح تحذيرات",
    "pin": "📌 تثبيت", "unpin": "📍 إلغاء تثبيت", "lock": "🔒 قفل", "unlock": "🔓 فتح",
    "filter_add": "🚫➕ إضافة كلمة ممنوعة", "filter_del": "🚫➖ حذف كلمة ممنوعة",
    "note_add": "🗒➕ إضافة ملاحظة", "note_del": "🗒➖ حذف ملاحظة", "broadcast": "📢 إرسال فوري",
}


@router.callback_query(F.data == "m:activity")
@_admin_only_callback
async def cb_activity(callback: CallbackQuery):
    session = get_session()
    try:
        logs = session.query(ActivityLog).order_by(ActivityLog.id.desc()).limit(20).all()
        lines = ["📋 <b>آخر ٢٠ عملية إدارية:</b>\n"]
        for log in logs:
            label = ACTION_LABELS.get(log.action, log.action)
            when = log.created_at.strftime("%m-%d %H:%M")
            line = f"{when} — {label} — {log.actor_name}"
            if log.target_name:
                line += f" ← {log.target_name}"
            if log.details:
                line += f" ({log.details})"
            lines.append(line)
        if not logs:
            lines.append("لسه مفيش أي نشاط مسجل.")
        await callback.message.edit_text("\n".join(lines), reply_markup=kb([[("« رجوع", "m:main")]]))
    finally:
        session.close()
    await callback.answer()


# ---------------- إدارة سريعة من الخاص (بدون الدخول للجروب) ----------------

@router.callback_query(F.data == "m:moderation")
@_admin_only_callback
async def cb_moderation(callback: CallbackQuery):
    session = get_session()
    try:
        groups = session.query(GroupSettings).all()
        if not groups:
            await callback.message.edit_text(
                "لسه مفيش جروب مسجل.",
                reply_markup=kb([[("« رجوع", "m:main")]]),
            )
            await callback.answer()
            return
        rows = [[(group_label(g), f"m:mod_group:{g.chat_id}")] for g in groups]
        rows.append([("« رجوع", "m:main")])
        await callback.message.edit_text("🛡 اختار الجروب اللي عايز تدير فيه عضو:", reply_markup=kb(rows))
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data.startswith("m:mod_group:"))
@_admin_only_callback
async def cb_mod_group(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    rows = [
        [("🚫 حظر عضو", f"m:mod_action:ban:{chat_id}")],
        [("✅ رفع حظر عن عضو", f"m:mod_action:unban:{chat_id}")],
        [("🔇 كتم عضو", f"m:mod_action:mute:{chat_id}")],
        [("🔊 فك كتم عضو", f"m:mod_action:unmute:{chat_id}")],
        [("« رجوع", "m:moderation")],
    ]
    await callback.message.edit_text(
        "اختار العملية، وبعدين ابعتلي فوروارد لرسالة من العضو أو آيدي التليجرام بتاعه:",
        reply_markup=kb(rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("m:mod_action:"))
@_admin_only_callback
async def cb_mod_action(callback: CallbackQuery):
    _, _, action, chat_id = callback.data.split(":")
    PENDING[callback.from_user.id] = {"action": "moderate", "mod_action": action, "chat_id": int(chat_id)}
    action_ar = {"ban": "حظر", "unban": "رفع حظر عن", "mute": "كتم", "unmute": "فك كتم عن"}[action]
    await callback.message.edit_text(
        f"ابعتلي فوروارد لرسالة من العضو اللي عايز {action_ar}ه، أو ابعت آيدي التليجرام بتاعه مباشرة:",
        reply_markup=kb([[("« إلغاء", f"m:mod_group:{chat_id}")]]),
    )
    await callback.answer()


# ---------------- استقبال الردود النصية للعمليات المعلقة ----------------

def has_pending_action(message: Message) -> bool:
    if message.chat.type != "private" or not message.text:
        return False
    pending = PENDING.get(message.from_user.id)
    return bool(pending) and is_bot_admin_id(message.from_user.id)


@router.message(has_pending_action)
async def handle_pending_text(message: Message):
    pending = PENDING.get(message.from_user.id)
    action = pending["action"]

    if action == "new_exam_title":
        PENDING[message.from_user.id] = {**pending, "action": "new_exam_time", "title": message.text.strip()}
        await message.answer("اكتب الوقت المسموح لكل سؤال بالثواني (مثال: 30):")
        return

    if action == "new_exam_time":
        if not message.text.strip().isdigit():
            await message.answer("اكتب رقم صحيح بالثواني.")
            return
        PENDING[message.from_user.id] = {
            **pending, "action": "exam_picker", "mode": "new",
            "time": int(message.text.strip()), "selected": set(),
        }
        text, markup = render_exam_picker(message.from_user.id)
        await message.answer(text, reply_markup=markup)
        return

    if action == "edit_exam_title":
        new_title = pending["title"] if message.text.strip() == "-" else message.text.strip()
        PENDING[message.from_user.id] = {**pending, "action": "edit_exam_time", "title": new_title}
        await message.answer(f"الوقت الحالي: {pending['time']} ثانية\n\nاكتب وقت جديد بالثواني، أو ابعت - عشان تسيبه زي ما هو:")
        return

    if action == "edit_exam_time":
        if message.text.strip() == "-":
            new_time = pending["time"]
        elif message.text.strip().isdigit():
            new_time = int(message.text.strip())
        else:
            await message.answer("اكتب رقم صحيح أو -.")
            return
        PENDING[message.from_user.id] = {**pending, "action": "exam_picker", "mode": "edit", "time": new_time}
        text, markup = render_exam_picker(message.from_user.id)
        await message.answer(text, reply_markup=markup)
        return

    if action == "q_text":
        PENDING[message.from_user.id] = {**pending, "action": "q_options", "text": message.text.strip()}
        await message.answer("اكتب الاختيارات، كل اختيار في سطر لوحده (اختيارين على الأقل):")
        return

    if action == "q_options":
        options = [line.strip() for line in message.text.split("\n") if line.strip()]
        if len(options) < 2:
            await message.answer("لازم اختيارين على الأقل، كل واحد في سطر.")
            return
        PENDING[message.from_user.id] = {**pending, "action": "q_correct", "options": options}
        opts_list = "\n".join(f"{i}) {o}" for i, o in enumerate(options))
        await message.answer(f"{opts_list}\n\nاكتب رقم الإجابة الصحيحة (0 = الأول):")
        return

    if action == "q_correct":
        if not message.text.strip().isdigit() or int(message.text.strip()) >= len(pending["options"]):
            await message.answer(f"اكتب رقم من 0 لـ {len(pending['options']) - 1}.")
            return
        PENDING[message.from_user.id] = {**pending, "action": "q_explanation", "correct_index": int(message.text.strip())}
        await message.answer("اكتب شرح الإجابة (أو ابعت - لتجاهله):")
        return

    if action == "q_explanation":
        explanation = "" if message.text.strip() == "-" else message.text.strip()
        PENDING[message.from_user.id] = {**pending, "action": "q_difficulty", "explanation": explanation}
        await message.answer("اكتب مستوى الصعوبة (easy / medium / hard)، أو ابعت - للمتوسط:")
        return

    if action == "q_difficulty":
        raw = message.text.strip().lower()
        difficulty = raw if raw in ("easy", "medium", "hard") else "medium"
        session = get_session()
        try:
            if pending["mode"] == "new":
                session.add(Question(
                    subject_id=pending["subject_id"], text=pending["text"],
                    options=json.dumps(pending["options"], ensure_ascii=False),
                    correct_index=pending["correct_index"], explanation=pending["explanation"],
                    difficulty=difficulty,
                ))
            else:
                q = session.query(Question).filter_by(id=pending["question_id"]).first()
                if q:
                    q.text = pending["text"]
                    q.options = json.dumps(pending["options"], ensure_ascii=False)
                    q.correct_index = pending["correct_index"]
                    q.explanation = pending["explanation"]
                    q.difficulty = difficulty
            session.commit()
        finally:
            session.close()
        PENDING.pop(message.from_user.id, None)
        await message.answer("✅ تم حفظ السؤال.", reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))
        return

    if action == "add_filter_word":
        parts = message.text.split("|", maxsplit=1)
        trigger = parts[0].strip().lower()
        reply = parts[1].strip() if len(parts) > 1 else ""
        if not trigger:
            await message.answer("اكتب الكلمة على الأقل، بالشكل: الكلمة | الرد")
            return
        session = get_session()
        try:
            session.add(FilterWord(chat_id=pending["chat_id"], trigger=trigger, reply=reply))
            session.commit()
            log_action(pending["chat_id"], message.from_user.id, message.from_user.full_name, "filter_add", details=trigger)
        finally:
            session.close()
        PENDING.pop(message.from_user.id, None)
        await message.answer(f"✅ تمت إضافة الكلمة الممنوعة: {trigger}", reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))
        return

    if action == "add_note":
        parts = message.text.split("|", maxsplit=1)
        if len(parts) < 2 or not parts[0].strip():
            await message.answer("اكتب بالشكل: الكلمة | المحتوى")
            return
        keyword, content = parts[0].strip().lower(), parts[1].strip()
        session = get_session()
        try:
            session.query(Note).filter_by(chat_id=pending["chat_id"], keyword=keyword).delete()
            session.add(Note(chat_id=pending["chat_id"], keyword=keyword, content=content))
            session.commit()
            log_action(pending["chat_id"], message.from_user.id, message.from_user.full_name, "note_add", details=keyword)
        finally:
            session.close()
        PENDING.pop(message.from_user.id, None)
        await message.answer(f"✅ تم حفظ الملاحظة: #{keyword}", reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))
        return

    if action == "broadcast_content":
        await message.bot.send_message(pending["chat_id"], message.text)
        log_action(pending["chat_id"], message.from_user.id, message.from_user.full_name, "broadcast", details=message.text[:40])
        PENDING.pop(message.from_user.id, None)
        await message.answer("✅ اتبعتت الرسالة فورًا.", reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))
        return

    if action == "add_subject":
        session = get_session()
        try:
            name = message.text.strip()
            if not session.query(Subject).filter_by(name=name).first():
                session.add(Subject(name=name))
                session.commit()
                await message.answer(f"✅ تمت إضافة مادة: {name}", reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))
            else:
                await message.answer("المادة دي موجودة أصلاً.")
        finally:
            session.close()
        PENDING.pop(message.from_user.id, None)
        return

    if action == "schedule_content":
        PENDING[message.from_user.id] = {
            "action": "schedule_confirm",
            "chat_id": pending["chat_id"],
            "content": message.text,
        }
        rows = [[(t, f"m:sched_when:{m}")] for t, m in TIMING_OPTIONS]
        rows.append([("« إلغاء", "m:schedule")])
        await message.answer("تتبعت إمتى؟", reply_markup=kb(rows))
        return

    if action == "edit_rules":
        session = get_session()
        try:
            g = session.query(GroupSettings).filter_by(chat_id=pending["chat_id"]).first()
            if g:
                g.rules_text = message.text
                session.commit()
                await message.answer("✅ اتحدثت القوانين.", reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))
        finally:
            session.close()
        PENDING.pop(message.from_user.id, None)
        return

    if action == "edit_welcome":
        session = get_session()
        try:
            g = session.query(GroupSettings).filter_by(chat_id=pending["chat_id"]).first()
            if g:
                g.welcome_text = message.text
                session.commit()
                await message.answer("✅ اتحدثت رسالة الترحيب.", reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))
        finally:
            session.close()
        PENDING.pop(message.from_user.id, None)
        return

    if action == "add_admin":
        target_id = None
        target_name = ""
        if message.forward_from:
            target_id = message.forward_from.id
            target_name = message.forward_from.full_name
        elif message.text and message.text.strip().isdigit():
            target_id = int(message.text.strip())
            target_name = ""
        else:
            await message.answer("محتاج فوروارد لرسالة من الشخص أو آيدي رقمي.")
            return

        session = get_session()
        try:
            if not session.query(Admin).filter_by(user_id=target_id).first():
                session.add(Admin(user_id=target_id, name=target_name))
                session.commit()
            await message.answer(f"✅ تمت إضافة {target_name or target_id} كمشرف.", reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))
        finally:
            session.close()
        PENDING.pop(message.from_user.id, None)
        return

    if action == "moderate":
        target_id = None
        target_name = ""
        if message.forward_from:
            target_id = message.forward_from.id
            target_name = message.forward_from.full_name
        elif message.text and message.text.strip().isdigit():
            target_id = int(message.text.strip())
        else:
            await message.answer("محتاج فوروارد لرسالة من الشخص أو آيدي رقمي.")
            return

        chat_id = pending["chat_id"]
        mod_action = pending["mod_action"]
        NO_PERMS_MOD = ChatPermissions(
            can_send_messages=False, can_send_media_messages=False,
            can_send_polls=False, can_send_other_messages=False, can_add_web_page_previews=False,
        )
        FULL_PERMS_MOD = ChatPermissions(
            can_send_messages=True, can_send_media_messages=True,
            can_send_polls=True, can_send_other_messages=True, can_add_web_page_previews=True,
        )
        try:
            if mod_action == "ban":
                await message.bot.ban_chat_member(chat_id, target_id)
                result_text = f"🚫 تم حظر {target_name or target_id}."
            elif mod_action == "unban":
                await message.bot.unban_chat_member(chat_id, target_id)
                result_text = f"✅ تم رفع الحظر عن {target_name or target_id}."
            elif mod_action == "mute":
                await message.bot.restrict_chat_member(chat_id, target_id, NO_PERMS_MOD)
                result_text = f"🔇 تم كتم {target_name or target_id}."
            else:  # unmute
                await message.bot.restrict_chat_member(chat_id, target_id, FULL_PERMS_MOD)
                result_text = f"🔊 تم فك الكتم عن {target_name or target_id}."

            log_action(chat_id, message.from_user.id, message.from_user.full_name, mod_action, target_id, target_name, details="من لوحة الخاص")
            await message.answer(result_text, reply_markup=kb([[("« القائمة الرئيسية", "m:main")]]))
        except Exception as e:
            await message.answer(f"❌ حصل خطأ: {e}\nتأكد إن البوت أدمن في الجروب ده وإن الآيدي صحيح.")
        PENDING.pop(message.from_user.id, None)
        return
