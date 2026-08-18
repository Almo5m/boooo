import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from database import get_session, ScheduledMessage

scheduler = AsyncIOScheduler()


async def _send_scheduled(bot, message_id: int):
    session = get_session()
    try:
        msg = session.query(ScheduledMessage).filter_by(id=message_id).first()
        if not msg or not msg.is_active:
            return
        sent = await bot.send_message(msg.chat_id, msg.content)
        if msg.pin:
            try:
                await bot.pin_chat_message(msg.chat_id, sent.message_id)
            except Exception:
                pass
        if msg.run_at:  # رسالة لمرة واحدة، عطّلها بعد الإرسال
            msg.is_active = False
            session.commit()
    finally:
        session.close()


def schedule_message(bot, sched_msg: ScheduledMessage):
    job_id = f"sched_{sched_msg.id}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    if sched_msg.cron_expr:
        parts = sched_msg.cron_expr.split()
        if len(parts) == 5:
            minute, hour, day, month, dow = parts
            trigger = CronTrigger(minute=minute, hour=hour, day=day, month=month, day_of_week=dow)
            scheduler.add_job(_send_scheduled, trigger, args=[bot, sched_msg.id], id=job_id)
    elif sched_msg.run_at and sched_msg.run_at > datetime.datetime.utcnow():
        trigger = DateTrigger(run_date=sched_msg.run_at)
        scheduler.add_job(_send_scheduled, trigger, args=[bot, sched_msg.id], id=job_id)


def load_all_scheduled(bot):
    session = get_session()
    try:
        for msg in session.query(ScheduledMessage).filter_by(is_active=True).all():
            schedule_message(bot, msg)
    finally:
        session.close()
