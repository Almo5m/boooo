from aiogram import Router, F
from aiogram.types import Message
from database import get_session, FilterWord, Note
from bot.handlers.welcome import get_or_create_settings
from bot.handlers.arabic_commands import is_group_admin, match_command

router = Router()

LOCK_TYPES = {"الروابط": "links", "التوجيه": "forward", "الاستيكرات": "stickers"}

ADDFILTER_WORDS = {"اضف كلمة ممنوعة", "ضيف كلمة ممنوعة"}
DELFILTER_WORDS = {"احذف كلمة ممنوعة", "امسح كلمة ممنوعة"}
SAVE_WORDS = {"احفظ", "احفظها"}
NOTES_WORDS = {"الملاحظات"}
LOCK_WORDS = {"اقفل"}
UNLOCK_WORDS = {"افتح"}

CTRL_WORDS = ADDFILTER_WORDS | DELFILTER_WORDS | SAVE_WORDS | NOTES_WORDS | LOCK_WORDS | UNLOCK_WORDS


def is_filter_command(message: Message) -> bool:
    if not message.text:
        return False
    cmd, _ = match_command(message.text, CTRL_WORDS)
    return cmd is not None


@router.message(is_filter_command)
async def filter_control_router(message: Message):
    text = message.text.strip()
    cmd, rest = match_command(text, CTRL_WORDS)
    rest = rest.strip()

    if cmd in ADDFILTER_WORDS:
        if not await is_group_admin(message):
            return
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("استخدم: اضف كلمة ممنوعة [الكلمة] [الرد]")
            return
        trigger, reply = parts
        session = get_session()
        try:
            session.add(FilterWord(chat_id=message.chat.id, trigger=trigger.lower(), reply=reply))
            session.commit()
            await message.reply(f"✅ تمت إضافة الفلتر على كلمة: {trigger}")
        finally:
            session.close()
        return

    if cmd in DELFILTER_WORDS:
        if not await is_group_admin(message):
            return
        if not rest:
            await message.reply("استخدم: احذف كلمة ممنوعة [الكلمة]")
            return
        session = get_session()
        try:
            session.query(FilterWord).filter_by(chat_id=message.chat.id, trigger=rest.lower()).delete()
            session.commit()
            await message.reply("✅ تم حذف الفلتر.")
        finally:
            session.close()
        return

    if cmd in SAVE_WORDS:
        if not await is_group_admin(message):
            return
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            await message.reply("استخدم: احفظ [الكلمة_المفتاحية] [المحتوى]")
            return
        keyword, content = parts
        session = get_session()
        try:
            session.query(Note).filter_by(chat_id=message.chat.id, keyword=keyword.lower()).delete()
            session.add(Note(chat_id=message.chat.id, keyword=keyword.lower(), content=content))
            session.commit()
            await message.reply(f"✅ تم حفظ الملاحظة تحت الكلمة: #{keyword}")
        finally:
            session.close()
        return

    if cmd in NOTES_WORDS:
        session = get_session()
        try:
            notes = session.query(Note).filter_by(chat_id=message.chat.id).all()
            if not notes:
                await message.reply("مفيش ملاحظات محفوظة.")
                return
            text_out = "🗒 <b>الملاحظات المحفوظة:</b>\n" + "\n".join(f"• #{n.keyword}" for n in notes)
            await message.reply(text_out)
        finally:
            session.close()
        return

    if cmd in LOCK_WORDS or cmd in UNLOCK_WORDS:
        if not await is_group_admin(message):
            return
        target_key = LOCK_TYPES.get(rest)
        if not target_key:
            await message.reply("استخدم: اقفل الروابط | التوجيه | الاستيكرات (أو افتح بدل اقفل)")
            return
        session = get_session()
        try:
            settings = get_or_create_settings(session, message.chat.id)
            setattr(settings, f"lock_{target_key}", cmd in LOCK_WORDS)
            session.commit()
            action = "قفل" if cmd in LOCK_WORDS else "فتح"
            await message.reply(f"{'🔒' if cmd in LOCK_WORDS else '🔓'} تم {action}: {rest}")
        finally:
            session.close()
        return


# هاندلر عام لأي رسالة نصية عادية عشان يطبق القفل ويرد على الملاحظات والفلاتر
@router.message(F.text)
async def handle_text(message: Message):
    session = get_session()
    try:
        settings = get_or_create_settings(session, message.chat.id)

        if settings.lock_links and ("http://" in message.text or "https://" in message.text or "t.me/" in message.text):
            if not await is_group_admin(message):
                try:
                    await message.delete()
                except Exception:
                    pass
                return

        if message.text.startswith("#"):
            keyword = message.text[1:].strip().lower()
            note = session.query(Note).filter_by(chat_id=message.chat.id, keyword=keyword).first()
            if note:
                await message.reply(note.content)
                return

        filters_ = session.query(FilterWord).filter_by(chat_id=message.chat.id).all()
        low = message.text.lower()
        for f in filters_:
            if f.trigger in low:
                try:
                    await message.delete()
                except Exception:
                    pass
                if f.reply:
                    await message.answer(f.reply)
                return
    finally:
        session.close()


@router.message(F.forward_date)
async def handle_forward(message: Message):
    session = get_session()
    try:
        settings = get_or_create_settings(session, message.chat.id)
        if settings.lock_forward and not await is_group_admin(message):
            try:
                await message.delete()
            except Exception:
                pass
    finally:
        session.close()


@router.message(F.sticker)
async def handle_sticker(message: Message):
    session = get_session()
    try:
        settings = get_or_create_settings(session, message.chat.id)
        if settings.lock_stickers and not await is_group_admin(message):
            try:
                await message.delete()
            except Exception:
                pass
    finally:
        session.close()
