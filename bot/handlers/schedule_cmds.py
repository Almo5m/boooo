from aiogram import Router
from aiogram.types import Message
from database import get_session, ScheduledMessage
from bot.handlers.arabic_commands import is_group_admin, match_command

router = Router()

LIST_WORDS = {"الرسايل المجدولة", "الرسائل المجدولة"}
CANCEL_WORDS = {"الغاء الجدولة", "الغاء رسالة مجدولة"}
SCHEDULE_CMD_WORDS = LIST_WORDS | CANCEL_WORDS


def is_schedule_command(message: Message) -> bool:
    if not message.text:
        return False
    cmd, _ = match_command(message.text, SCHEDULE_CMD_WORDS)
    return cmd is not None


@router.message(is_schedule_command)
async def schedule_router(message: Message):
    if not await is_group_admin(message):
        return
    text = message.text.strip()
    cmd, rest = match_command(text, SCHEDULE_CMD_WORDS)
    rest = rest.strip()

    if cmd in LIST_WORDS:
        session = get_session()
        try:
            items = session.query(ScheduledMessage).filter_by(chat_id=message.chat.id, is_active=True).all()
            if not items:
                await message.reply("مفيش رسائل مجدولة حاليًا. تقدر تضيف من لوحة التحكم.")
                return
            result = "🗓 <b>الرسائل المجدولة:</b>\n"
            for it in items:
                when = it.cron_expr or str(it.run_at)
                result += f"#{it.id} — {when} — {it.content[:40]}\n"
            await message.reply(result)
        finally:
            session.close()
        return

    if cmd in CANCEL_WORDS:
        if not rest.isdigit():
            await message.reply("استخدم: الغاء الجدولة [رقم_الرسالة]")
            return
        session = get_session()
        try:
            item = session.query(ScheduledMessage).filter_by(id=int(rest), chat_id=message.chat.id).first()
            if not item:
                await message.reply("❌ مش لاقي رسالة بالرقم ده.")
                return
            item.is_active = False
            session.commit()
            await message.reply("✅ تم إلغاء الرسالة المجدولة.")
        finally:
            session.close()
        return
