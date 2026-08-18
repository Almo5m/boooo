from aiogram import Router
from aiogram.types import Message
from bot.ai_engine import get_ai_reply, BOT_PERSONA_NAME

router = Router()

TRIGGER_WORDS = [BOT_PERSONA_NAME.lower(), "يا بوت", "يا مساعد", "يا روبوت"]


async def is_ai_trigger(message: Message) -> bool:
    if not message.text:
        return False
    if message.reply_to_message and message.reply_to_message.from_user:
        me = await message.bot.me()
        if message.reply_to_message.from_user.id == me.id:
            return True
    low = message.text.lower()
    return any(trigger in low for trigger in TRIGGER_WORDS)


@router.message(is_ai_trigger)
async def ai_chat_reply(message: Message):
    await message.bot.send_chat_action(message.chat.id, "typing")
    reply = await get_ai_reply(message.chat.id, message.from_user.full_name, message.text)
    await message.reply(reply)
