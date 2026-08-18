import datetime
from aiogram import Router, F
from aiogram.types import Message, ChatPermissions
from aiogram.filters import Command
from database import get_session, Warning
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


async def is_group_admin(message: Message) -> bool:
    member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
    return member.status in ("administrator", "creator")


def get_target(message: Message):
    if message.reply_to_message:
        return message.reply_to_message.from_user
    return None


@router.message(Command("ban"))
async def ban_user(message: Message):
    if not await is_group_admin(message):
        return
    target = get_target(message)
    if not target:
        await message.reply("رد على رسالة الشخص اللي عايز تحظره واكتب /ban")
        return
    await message.bot.ban_chat_member(message.chat.id, target.id)
    await message.reply(f"🚫 تم حظر {target.full_name}.")


@router.message(Command("unban"))
async def unban_user(message: Message):
    if not await is_group_admin(message):
        return
    target = get_target(message)
    if not target:
        await message.reply("رد على رسالة الشخص وابعت /unban")
        return
    await message.bot.unban_chat_member(message.chat.id, target.id)
    await message.reply(f"✅ تم رفع الحظر عن {target.full_name}.")


@router.message(Command("mute"))
async def mute_user(message: Message):
    if not await is_group_admin(message):
        return
    target = get_target(message)
    if not target:
        await message.reply("رد على رسالة الشخص واكتب /mute [دقايق اختياري]")
        return
    parts = message.text.split()
    until = None
    if len(parts) > 1 and parts[1].isdigit():
        until = datetime.datetime.now() + datetime.timedelta(minutes=int(parts[1]))
    await message.bot.restrict_chat_member(message.chat.id, target.id, NO_PERMS, until_date=until)
    await message.reply(f"🔇 تم كتم {target.full_name}" + (f" لمدة {parts[1]} دقيقة." if until else " (بدون تحديد مدة)."))


@router.message(Command("unmute"))
async def unmute_user(message: Message):
    if not await is_group_admin(message):
        return
    target = get_target(message)
    if not target:
        await message.reply("رد على رسالة الشخص واكتب /unmute")
        return
    await message.bot.restrict_chat_member(message.chat.id, target.id, FULL_PERMS)
    await message.reply(f"🔊 تم فك الكتم عن {target.full_name}.")


@router.message(Command("kick"))
async def kick_user(message: Message):
    if not await is_group_admin(message):
        return
    target = get_target(message)
    if not target:
        await message.reply("رد على رسالة الشخص واكتب /kick")
        return
    await message.bot.ban_chat_member(message.chat.id, target.id)
    await message.bot.unban_chat_member(message.chat.id, target.id)
    await message.reply(f"👢 تم طرد {target.full_name}.")


@router.message(Command("warn"))
async def warn_user(message: Message):
    if not await is_group_admin(message):
        return
    target = get_target(message)
    if not target:
        await message.reply("رد على رسالة الشخص واكتب /warn [السبب]")
        return
    parts = message.text.split(maxsplit=1)
    reason = parts[1] if len(parts) > 1 else "بدون سبب محدد"

    session = get_session()
    try:
        settings = get_or_create_settings(session, message.chat.id)
        w = Warning(chat_id=message.chat.id, user_id=target.id, reason=reason)
        session.add(w)
        session.commit()
        count = session.query(Warning).filter_by(chat_id=message.chat.id, user_id=target.id).count()

        if count >= settings.max_warnings:
            await message.bot.ban_chat_member(message.chat.id, target.id)
            session.query(Warning).filter_by(chat_id=message.chat.id, user_id=target.id).delete()
            session.commit()
            await message.reply(f"⛔️ {target.full_name} وصل لحد التحذيرات ({settings.max_warnings}) وتم حظره تلقائيًا.")
        else:
            await message.reply(
                f"⚠️ تحذير لـ {target.full_name} ({count}/{settings.max_warnings})\nالسبب: {reason}"
            )
    finally:
        session.close()


@router.message(Command("warns"))
async def show_warns(message: Message):
    target = get_target(message) or message.from_user
    session = get_session()
    try:
        count = session.query(Warning).filter_by(chat_id=message.chat.id, user_id=target.id).count()
        await message.reply(f"⚠️ عدد تحذيرات {target.full_name}: {count}")
    finally:
        session.close()


@router.message(Command("resetwarns"))
async def reset_warns(message: Message):
    if not await is_group_admin(message):
        return
    target = get_target(message)
    if not target:
        await message.reply("رد على رسالة الشخص واكتب /resetwarns")
        return
    session = get_session()
    try:
        session.query(Warning).filter_by(chat_id=message.chat.id, user_id=target.id).delete()
        session.commit()
        await message.reply(f"✅ تم مسح تحذيرات {target.full_name}.")
    finally:
        session.close()


@router.message(Command("pin"))
async def pin_message(message: Message):
    if not await is_group_admin(message):
        return
    if not message.reply_to_message:
        await message.reply("رد على الرسالة اللي عايز تثبتها واكتب /pin")
        return
    await message.bot.pin_chat_message(message.chat.id, message.reply_to_message.message_id)
    await message.reply("📌 تم التثبيت.")


@router.message(Command("unpin"))
async def unpin_message(message: Message):
    if not await is_group_admin(message):
        return
    await message.bot.unpin_all_chat_messages(message.chat.id)
    await message.reply("✅ تم إلغاء تثبيت كل الرسائل.")
