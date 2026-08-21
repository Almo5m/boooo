import datetime
from aiogram import Router
from aiogram.types import Message, ChatPermissions
from database import get_session, Warning, Admin
from bot.handlers.welcome import get_or_create_settings

router = Router()

NO_PERMS = ChatPermissions(
    can_send_messages=False, can_send_media_messages=False,
    can_send_polls=False, can_send_other_messages=False,
    can_add_web_page_previews=False,
)
FULL_PERMS = ChatPermissions(
    can_send_messages=True, can_send_media_messages=True,
    can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True,
)


def is_bot_admin_id(user_id: int) -> bool:
    """
    الأساس الحقيقي لصلاحية التحكم: المالك (OWNER_IDS) أو مشرف مضاف من الخاص.
    """
    from bot.bot import is_owner
    if is_owner(user_id):
        return True
    session = get_session()
    try:
        return session.query(Admin).filter_by(user_id=user_id).first() is not None
    finally:
        session.close()


async def is_group_admin(message: Message) -> bool:
    """
    التحكم في البوت مقصور على المالك (OWNER_IDS) والمشرفين المضافين من الخاص،
    مش أي أدمن تليجرام عادي في الجروب.
    """
    return is_bot_admin_id(message.from_user.id)


def get_target(message: Message):
    if message.reply_to_message:
        return message.reply_to_message.from_user
    return None


def match_command(text: str, known_commands: set) -> tuple:
    """
    بيدور على أطول عبارة من known_commands تكون بداية النص (فاصلة بمسافة أو نهاية النص)،
    عشان "عدل القوانين" تتفهم صح حتى لو فيها أكتر من كلمة. بيرجع (الأمر المطابق, باقي النص).
    """
    words = text.strip().split()
    max_len = max((len(c.split()) for c in known_commands), default=1)
    for n in range(min(max_len, len(words)), 0, -1):
        candidate = " ".join(words[:n])
        if candidate in known_commands:
            rest = " ".join(words[n:])
            return candidate, rest
    return None, text


def first_word(text: str) -> str:
    return text.strip().split()[0] if text.strip() else ""


def rest_of_text(text: str) -> str:
    parts = text.strip().split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


# كل عبارة بتؤدي لأمر معين - المطابقة بتاخد أول كلمة أو أكتر من بداية الرسالة
BAN_WORDS = {"احظر", "احظره", "حظر"}
UNBAN_WORDS = {"فك الحظر", "الغاء الحظر"}
MUTE_WORDS = {"اكتم", "اكتمه", "كتم"}
UNMUTE_WORDS = {"فك الكتم", "الغاء الكتم"}
KICK_WORDS = {"اطرد", "اطرده", "طرد"}
WARN_WORDS = {"حذر", "حذره", "تحذير"}
WARNS_COUNT_WORDS = {"تحذيراته", "تحذيراتي"}
RESET_WARNS_WORDS = {"امسح تحذيراته", "الغاء تحذيراته"}
PIN_WORDS = {"ثبت", "ثبتها"}
UNPIN_WORDS = {"الغاء التثبيت"}
RULES_WORDS = {"القوانين", "القواعد"}
SET_RULES_WORDS = {"عدل القوانين", "غير القوانين"}
SET_WELCOME_WORDS = {"عدل الترحيب", "غير الترحيب"}


ALL_COMMAND_WORDS = (
    BAN_WORDS | UNBAN_WORDS | MUTE_WORDS | UNMUTE_WORDS | KICK_WORDS | WARN_WORDS
    | WARNS_COUNT_WORDS | RESET_WARNS_WORDS | PIN_WORDS | UNPIN_WORDS | RULES_WORDS
    | SET_RULES_WORDS | SET_WELCOME_WORDS
)


def is_arabic_admin_command(message: Message) -> bool:
    if not message.text or message.chat.type == "private":
        return False
    cmd, _ = match_command(message.text, ALL_COMMAND_WORDS)
    return cmd is not None


@router.message(is_arabic_admin_command)
async def arabic_admin_router(message: Message):
    text = message.text.strip()
    cmd, rest = match_command(text, ALL_COMMAND_WORDS)
    cmd_norm = cmd

    # الأوامر اللي محتاجة رد على رسالة عضو ومحتاجة صلاحية أدمن
    admin_reply_commands = BAN_WORDS | UNBAN_WORDS | MUTE_WORDS | UNMUTE_WORDS | KICK_WORDS | WARN_WORDS | RESET_WARNS_WORDS

    if cmd in admin_reply_commands or cmd_norm in admin_reply_commands:
        if not await is_group_admin(message):
            return
        target = get_target(message)
        if not target:
            await message.reply("لازم ترد على رسالة الشخص اللي عايز تعمل عليه الأمر ده 🙏")
            return

        if cmd in BAN_WORDS:
            await message.bot.ban_chat_member(message.chat.id, target.id)
            await message.reply(f"🚫 تم حظر {target.full_name}.")
            return

        if cmd_norm in UNBAN_WORDS or cmd in UNBAN_WORDS:
            await message.bot.unban_chat_member(message.chat.id, target.id)
            await message.reply(f"✅ تم رفع الحظر عن {target.full_name}.")
            return

        if cmd in MUTE_WORDS:
            arg = rest.split()[0] if rest.split() else ""
            until = None
            if arg.isdigit():
                until = datetime.datetime.now() + datetime.timedelta(minutes=int(arg))
            await message.bot.restrict_chat_member(message.chat.id, target.id, NO_PERMS, until_date=until)
            extra = f" لمدة {arg} دقيقة" if until else ""
            await message.reply(f"🔇 تم كتم {target.full_name}{extra}.")
            return

        if cmd_norm in UNMUTE_WORDS or cmd in UNMUTE_WORDS:
            await message.bot.restrict_chat_member(message.chat.id, target.id, FULL_PERMS)
            await message.reply(f"🔊 تم فك الكتم عن {target.full_name}.")
            return

        if cmd in KICK_WORDS:
            await message.bot.ban_chat_member(message.chat.id, target.id)
            await message.bot.unban_chat_member(message.chat.id, target.id)
            await message.reply(f"👢 تم طرد {target.full_name}.")
            return

        if cmd in WARN_WORDS:
            reason = rest.strip() or "بدون سبب محدد"
            session = get_session()
            try:
                settings = get_or_create_settings(session, message.chat.id)
                session.add(Warning(chat_id=message.chat.id, user_id=target.id, reason=reason))
                session.commit()
                count = session.query(Warning).filter_by(chat_id=message.chat.id, user_id=target.id).count()
                if count >= settings.max_warnings:
                    await message.bot.ban_chat_member(message.chat.id, target.id)
                    session.query(Warning).filter_by(chat_id=message.chat.id, user_id=target.id).delete()
                    session.commit()
                    await message.reply(f"⛔️ {target.full_name} وصل لحد التحذيرات ({settings.max_warnings}) وتم حظره تلقائيًا.")
                else:
                    await message.reply(f"⚠️ تحذير لـ {target.full_name} ({count}/{settings.max_warnings})\nالسبب: {reason}")
            finally:
                session.close()
            return

        if cmd_norm in RESET_WARNS_WORDS or cmd in RESET_WARNS_WORDS:
            session = get_session()
            try:
                session.query(Warning).filter_by(chat_id=message.chat.id, user_id=target.id).delete()
                session.commit()
                await message.reply(f"✅ تم مسح تحذيرات {target.full_name}.")
            finally:
                session.close()
            return

    # تحذيرات المستخدم (رد أو نفسه)
    if cmd in WARNS_COUNT_WORDS:
        target = get_target(message) or message.from_user
        session = get_session()
        try:
            count = session.query(Warning).filter_by(chat_id=message.chat.id, user_id=target.id).count()
            await message.reply(f"⚠️ عدد تحذيرات {target.full_name}: {count}")
        finally:
            session.close()
        return

    # تثبيت / إلغاء تثبيت
    if cmd in PIN_WORDS:
        if not await is_group_admin(message):
            return
        if not message.reply_to_message:
            await message.reply("رد على الرسالة اللي عايز تثبتها واكتب: ثبت")
            return
        await message.bot.pin_chat_message(message.chat.id, message.reply_to_message.message_id)
        await message.reply("📌 تم التثبيت.")
        return

    if cmd_norm in UNPIN_WORDS or cmd in UNPIN_WORDS:
        if not await is_group_admin(message):
            return
        await message.bot.unpin_all_chat_messages(message.chat.id)
        await message.reply("✅ تم إلغاء تثبيت كل الرسائل.")
        return

    # القوانين
    if cmd in RULES_WORDS:
        session = get_session()
        try:
            settings = get_or_create_settings(session, message.chat.id)
            await message.reply(f"📜 <b>قوانين الجروب:</b>\n\n{settings.rules_text}")
        finally:
            session.close()
        return

    if cmd_norm in SET_RULES_WORDS or cmd in SET_RULES_WORDS:
        if not await is_group_admin(message):
            return
        new_rules = rest.strip()
        if not new_rules:
            await message.reply("اكتب: عدل القوانين ثم النص الجديد")
            return
        session = get_session()
        try:
            settings = get_or_create_settings(session, message.chat.id)
            settings.rules_text = new_rules
            session.commit()
            await message.reply("✅ تم تحديث القوانين.")
        finally:
            session.close()
        return

    if cmd_norm in SET_WELCOME_WORDS or cmd in SET_WELCOME_WORDS:
        if not await is_group_admin(message):
            return
        new_text = rest.strip()
        if not new_text:
            await message.reply("اكتب: عدل الترحيب ثم النص الجديد (استخدم {الاسم} لاسم العضو)")
            return
        session = get_session()
        try:
            settings = get_or_create_settings(session, message.chat.id)
            settings.welcome_text = new_text.replace("{الاسم}", "{name}")
            session.commit()
            await message.reply("✅ تم تحديث رسالة الترحيب.")
        finally:
            session.close()
        return
