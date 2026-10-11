import re
from aiogram import Router
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest
from bot.ai_engine import get_ai_reply, BOT_PERSONA_NAME
from bot.formatting import format_ai_reply, to_plain

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


TELEGRAM_LIMIT = 4000  # الحد الفعلي 4096، وسايبين هامش أمان


@router.message(is_ai_trigger)
async def ai_chat_reply(message: Message):
    await message.bot.send_chat_action(message.chat.id, "typing")
    raw_reply = await get_ai_reply(message.chat.id, message.from_user.full_name, message.text)
    formatted = format_ai_reply(raw_reply)

    # رد طويل: بنبعته نص عادي مقسّم عشان وسوم HTML مايتقطعش نصها
    if len(formatted) > TELEGRAM_LIMIT:
        plain = to_plain(formatted)
        for i in range(0, len(plain), TELEGRAM_LIMIT):
            await message.reply(plain[i:i + TELEGRAM_LIMIT], parse_mode=None)
        return

    try:
        await message.reply(formatted)
    except TelegramBadRequest:
        # لو تليجرام مقدرش يقرا التنسيق لأي سبب، نبعت الرد كنص عادي بدل ما الطالب مايستلمش حاجة
        await message.reply(to_plain(formatted), parse_mode=None)
