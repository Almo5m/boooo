from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import Message
from database import get_session, GroupSettings


class GroupRegistrationMiddleware(BaseMiddleware):
    """
    بتسجل أي جروب البوت بيستقبل فيه رسالة في قاعدة البيانات فورًا،
    بغض النظر عن أي هاندلر هيرد على الرسالة دي في النهاية.
    من غيرها، لو أول رسائل يستقبلها البوت في الجروب كانت كلها سلام/أسئلة
    بيرد عليها الـ AI مباشرة، الجروب ما كانش بيتسجل في لوحة التحكم أبدًا.
    """

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if event.chat.type in ("group", "supergroup"):
            session = get_session()
            try:
                exists = session.query(GroupSettings).filter_by(chat_id=event.chat.id).first()
                if not exists:
                    session.add(GroupSettings(chat_id=event.chat.id))
                    session.commit()
            finally:
                session.close()
        return await handler(event, data)
