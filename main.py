import os
import asyncio
import uvicorn
from dotenv import load_dotenv

from database import init_db
from bot.bot import bot, dp
from bot.handlers import general, welcome, arabic_commands, quiz, schedule_cmds, ai_chat, filters
from dashboard.app import app as dashboard_app, set_bot_instance
from scheduler import scheduler, load_all_scheduled

load_dotenv()

# الترتيب مهم: الأوامر المحددة الأول، وبعدين الذكاء الاصطناعي، وفي الآخر الفلاتر العامة (بتمسك أي رسالة نصية)
dp.include_router(general.router)
dp.include_router(welcome.router)
dp.include_router(arabic_commands.router)
dp.include_router(quiz.router)
dp.include_router(schedule_cmds.router)
dp.include_router(ai_chat.router)
dp.include_router(filters.router)


async def run_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


async def run_dashboard():
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config(dashboard_app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    init_db()
    set_bot_instance(bot)
    load_all_scheduled(bot)
    scheduler.start()

    await asyncio.gather(
        run_bot(),
        run_dashboard(),
    )


if __name__ == "__main__":
    asyncio.run(main())
