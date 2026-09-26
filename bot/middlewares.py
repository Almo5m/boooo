import datetime
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message
from database import get_session, GroupSettings, BotUser


class GroupRegistrationMiddleware(BaseMiddleware):
    """
    بتسجل أي جروب البوت بيستقبل فيه رسالة في قاعدة البيانات فورًا (مع اسم
    الجروب الحقيقي)، وبتسجل أي مستخدم بعت للبوت رسالة (جروب أو خاص) في سجل
    المستخدمين - بغض النظر عن أي هاندلر هيرد على الرسالة دي في النهاية.
    من غيرها، لو أول رسائل يستقبلها البوت كانت كلها سلام/أسئلة بيرد عليها
    الـ AI مباشرة، الجروب أو المستخدم ما كانش بيتسجل في لوحة التحكم أبدًا.
    """

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        session = get_session()
        try:
            if event.chat.type in ("group", "supergroup"):
                g = session.query(GroupSettings).filter_by(chat_id=event.chat.id).first()
                title = event.chat.title or ""
                if not g:
                    session.add(GroupSettings(chat_id=event.chat.id, title=title))
                    session.commit()
                elif title and g.title != title:
                    g.title = title
                    session.commit()

            if event.from_user and not event.from_user.is_bot:
                u = session.query(BotUser).filter_by(user_id=event.from_user.id).first()
                now = datetime.datetime.utcnow()
                if not u:
                    session.add(BotUser(
                        user_id=event.from_user.id,
                        name=event.from_user.full_name or "",
                        username=event.from_user.username or "",
                        first_seen=now,
                        last_seen=now,
                        message_count=1,
                    ))
                else:
                    u.last_seen = now
                    u.message_count = (u.message_count or 0) + 1
                    if event.from_user.full_name:
                        u.name = event.from_user.full_name
                    if event.from_user.username:
                        u.username = event.from_user.username
                session.commit()
        finally:
            session.close()

        return await handler(event, data)
