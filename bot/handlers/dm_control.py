import json
import datetime
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document, ChatPermissions,
)
from aiogram.filters import Command

from database import (
    get_session, Subject, Question, Exam, ScheduledMessage, GroupSettings, Admin, ExamResult, BotUser,
    ActivityLog, Warning, log_action,
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
        [("🏆 نقاط الطلاب", "m:leaderboard")],
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
        lines = ["📚 <b>المواد:</b>\n"]
        for s in subjects:
            lines.append(f"• {s.name} ({len(s.questions)} سؤال)")
        if not subjects:
            lines.append("مفيش مواد بعد.")
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=kb([[("➕ إضافة مادة", "m:add_subject")], [("« رجوع", "m:main")]]),
        )
    finally:
        session.close()
    await callback.answer()


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
                "لازم تضيف مادة الأول قبل رفع الأسئلة.",
                reply_markup=kb([[("➕ إضافة مادة", "m:add_subject")], [("« رجوع", "m:main")]]),
            )
            await callback.answer()
            return
        rows = [[(f"{s.name} ({len(s.questions)})", f"m:upload:{s.id}")] for s in subjects]
        rows.append([("« رجوع", "m:main")])
        await callback.message.edit_text(
            "❓ <b>بنك الأسئلة</b>\nاختار المادة اللي عايز ترفعلها أسئلة من ملف إكسيل:",
            reply_markup=kb(rows),
        )
    finally:
        session.close()
    await callback.answer()


@router.callback_query(F.data.startswith("m:upload:"))
@_admin_only_callback
async def cb_upload(callback: CallbackQuery):
    subject_id = int(callback.data.split(":")[2])
    PENDING[callback.from_user.id] = {"action": "upload_questions", "subject_id": subject_id}
    await callback.message.edit_text(
        "📎 ابعت ملف إكسيل (.xlsx) بالأسئلة دلوقتي.\n\n"
        "ترتيب الأعمدة:\n"
        "A: نص السؤال | B,C,D,E: الاختيارات (لغاية 4) | F: رقم الإجابة الصحيحة (0=الأول) | G: شرح (اختياري) | H: الصعوبة (اختياري)",
        reply_markup=kb([[("« إلغاء", "m:questions")]]),
    )
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

        if not exams:
            rows.append([("« رجوع", "m:main")])
            text = "مفيش اختبارات لسه. اعملها من لوحة التحكم على الويب."
            if active_groups:
                text = "⏹ فيه اختبار شغال دلوقتي، تقدر توقفه من تحت.\n\n" + text
            await callback.message.edit_text(text, reply_markup=kb(rows))
            await callback.answer()
            return

        for e in exams:
            rows.append([
                (f"▶️ {e.title}", f"m:startexam:{e.id}"),
                ("📄 مراجعة", f"m:staticexam:{e.id}"),
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


# ---------------- الجدولة ----------------

@router.callback_query(F.data == "m:schedule")
@_admin_only_callback
async def cb_schedule(callback: CallbackQuery):
    session = get_session()
    try:
        items = session.query(ScheduledMessage).filter_by(is_active=True).order_by(ScheduledMessage.id.desc()).limit(10).all()
        lines = ["🗓 <b>الرسائل المجدولة النشطة:</b>\n"]
        for it in items:
            when = it.cron_expr or str(it.run_at)
            lines.append(f"#{it.id} — {when} — {it.content[:30]}")
        if not items:
            lines.append("مفيش رسائل مجدولة حاليًا.")
        await callback.message.edit_text(
            "\n".join(lines),
            reply_markup=kb([[("➕ رسالة جديدة", "m:add_schedule")], [("« رجوع", "m:main")]]),
        )
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


@router.callback_query(F.data.startswith("m:group_settings:"))
@_admin_only_callback
async def cb_group_settings(callback: CallbackQuery):
    chat_id = int(callback.data.split(":")[2])
    session = get_session()
    try:
        g = session.query(GroupSettings).filter_by(chat_id=chat_id).first()
        if not g:
            await callback.answer("الجروب مش موجود.", show_alert=True)
            return
        text = (
            f"⚙️ <b>إعدادات {group_label(g)}</b>\n\n"
            f"📜 القوانين:\n{g.rules_text[:200]}\n\n"
            f"👋 الترحيب:\n{g.welcome_text[:200]}\n\n"
            f"⚠️ حد التحذيرات: {g.max_warnings}"
        )
        rows = [
            [("✏️ تعديل القوانين", f"m:edit_rules:{chat_id}")],
            [("✏️ تعديل الترحيب", f"m:edit_welcome:{chat_id}")],
            [("« رجوع", "m:settings")],
        ]
        await callback.message.edit_text(text, reply_markup=kb(rows))
    finally:
        session.close()
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
    "note_add": "🗒➕ إضافة ملاحظة", "note_del": "🗒➖ حذف ملاحظة",
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
