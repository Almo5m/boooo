import os
import socket
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_IDS = [int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip()]

# على ويندوز، مشكلة IPv6/Happy-Eyeballs بتسبب أخطاء "semaphore timeout"
# بشكل متكرر عند الاتصال بـ api.telegram.org. إجبار الاتصال يبقى IPv4 بس
# بيحل المشكلة دي في أغلب الحالات. تقدر تلغيها بحط FORCE_IPV4=false في .env
FORCE_IPV4 = os.getenv("FORCE_IPV4", "true").lower() == "true"

session = AiohttpSession()
if FORCE_IPV4:
    session._connector_init["family"] = socket.AF_INET

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    session=session,
)
dp = Dispatcher()


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS
