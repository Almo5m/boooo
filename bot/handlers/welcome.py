from aiogram import Router
from aiogram.types import ChatMemberUpdated, Message
from database import get_session, GroupSettings

router = Router()


def get_or_create_settings(session, chat_id: int) -> GroupSettings:
    s = session.query(GroupSettings).filter_by(chat_id=chat_id).first()
    if not s:
        s = GroupSettings(chat_id=chat_id)
        session.add(s)
        session.commit()
        session.refresh(s)
    return s


@router.chat_member()
async def on_member_join(event: ChatMemberUpdated):
    old = event.old_chat_member.status
    new = event.new_chat_member.status
    if old in ("left", "kicked") and new in ("member",):
        session = get_session()
        try:
            settings = get_or_create_settings(session, event.chat.id)
            if settings.welcome_enabled:
                name = event.new_chat_member.user.full_name
                text = settings.welcome_text.replace("{name}", name)
                await event.bot.send_message(event.chat.id, text)
        finally:
            session.close()
