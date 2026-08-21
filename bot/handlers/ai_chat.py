import re
from aiogram import Router
from aiogram.types import Message
from bot.ai_engine import get_ai_reply, BOT_PERSONA_NAME

router = Router()

TRIGGER_WORDS = [BOT_PERSONA_NAME.lower(), "يا بوت", "يا مساعد", "يا روبوت"]

GREETING_PATTERNS = [
    "السلام عليكم", "سلام عليكم", "صباح الخير", "مساء الخير",
    "هاي", "هلا", "أهلا", "اهلا", "صباح النور", "مساء النور",
]


def _is_greeting(text: str) -> bool:
    low = text.strip().lower()
    return any(low.startswith(g) or g in low for g in GREETING_PATTERNS)


def _is_direct_question(text: str) -> bool:
    # سؤال واضح المعالم: بينتهي بعلامة استفهام، وقصير بما يكفي إنه موجه مش جزء من نقاش طويل
    stripped = text.strip()
    return (stripped.endswith("؟") or stripped.endswith("?")) and len(stripped) <= 200


async def is_ai_trigger(message: Message) -> bool:
    if not message.text:
        return False

    # رد على رسالة البوت نفسه
    if message.reply_to_message and message.reply_to_message.from_user:
        me = await message.bot.me()
        if message.reply_to_message.from_user.id == me.id:
            return True

    low = message.text.lower()

    # مناداة صريحة بالاسم
    if any(trigger in low for trigger in TRIGGER_WORDS):
        return True

    # في الخاص: أي رسالة تتفاعل معاها طالما مش أمر تحكم
    if message.chat.type == "private":
        return True

    # في الجروب: سلام أو سؤال مباشر بس (عشان مايردش على كل حاجة)
    return _is_greeting(message.text) or _is_direct_question(message.text)


@router.message(is_ai_trigger)
async def ai_chat_reply(message: Message):
    await message.bot.send_chat_action(message.chat.id, "typing")
    reply = await get_ai_reply(message.chat.id, message.from_user.full_name, message.text)
    await message.reply(reply)
