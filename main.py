import asyncio
from dotenv import load_dotenv

from database import init_db
from bot.bot import bot, dp
from bot.handlers import general, welcome, dm_control, arabic_commands, quiz, schedule_cmds, ai_chat, filters
from bot.middlewares import GroupRegistrationMiddleware
from scheduler import scheduler, load_all_scheduled

load_dotenv()

# بتسجل أي جروب فورًا في قاعدة البيانات قبل أي هاندلر تاني - عشان يظهر في قوائم لوحة الخاص
# حتى لو أول رسالة كانت سلام أو سؤال اتلقطت من الذكاء الاصطناعي مباشرة
dp.message.outer_middleware(GroupRegistrationMiddleware())

# الترتيب مهم:
# 1) عام (start/help للجروب)  2) الترحيب  3) لوحة تحكم الخاص (لها أولوية في الخاص)
# 4) أوامر الجروب العربي  5) الاختبارات  6) الجدولة
# 7) الذكاء الاصطناعي (بيمسك أي حاجة متردش عليها حاجة تانية)  8) الفلاتر العامة (آخر حاجة)
dp.include_router(general.router)
dp.include_router(welcome.router)
dp.include_router(dm_control.router)
dp.include_router(arabic_commands.router)
dp.include_router(quiz.router)
dp.include_router(schedule_cmds.router)
dp.include_router(ai_chat.router)
dp.include_router(filters.router)


async def main():
    init_db()
    load_all_scheduled(bot)
    scheduler.start()

    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
