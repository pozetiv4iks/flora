import asyncio
import logging
import sys
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from app.config import Config
from app.database import Database
from app.brain import FloraBrain

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

Config.validate()
db = Database()
brain = FloraBrain(db)

bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
bot_username = ""

GROUP = Config.ALLOWED_GROUP_CHAT_ID


def is_allowed_group(chat_id: int) -> bool:
    return chat_id == GROUP


def resolve_owner_user_id(message: Message) -> int:
    sender_id = message.from_user.id
    if Config.ALLOWED_USER_IDS and sender_id in Config.ALLOWED_USER_IDS:
        return sender_id
    registered_owner = db.get_group_chat_owner(message.chat.id)
    if registered_owner:
        return registered_owner
    if Config.ALLOWED_USER_IDS:
        return Config.ALLOWED_USER_IDS[0]
    return sender_id


@asynccontextmanager
async def typing_status(bot: Bot, chat_id: int):
    async def loop():
        try:
            while True:
                await bot.send_chat_action(chat_id=chat_id, action="typing")
                await asyncio.sleep(4.5)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in typing status loop: {e}")

    task = asyncio.create_task(loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def is_flora_mentioned(message: Message) -> bool:
    if message.reply_to_message and message.reply_to_message.from_user:
        me = await bot.get_me()
        if message.reply_to_message.from_user.id == me.id:
            return True

    text = (message.text or message.caption or "")
    if not text:
        return False

    lower_text = text.lower()
    if "flora" in lower_text or "флора" in lower_text:
        return True

    if message.entities and bot_username:
        for entity in message.entities:
            if entity.type == "mention":
                mention = text[entity.offset:entity.offset + entity.length]
                if mention.lower() == f"@{bot_username.lower()}":
                    return True
    return False


def _parse_event_time(event_time: str):
    if not event_time:
        return None
    try:
        parts = event_time.strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return hour, minute
    except (ValueError, IndexError):
        return None


def _is_in_remind_window(now: datetime, target_h: int, target_m: int, grace_minutes: int = 10) -> bool:
    target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
    return target <= now <= target + timedelta(minutes=grace_minutes)


def should_remind_plan(plan: dict) -> bool:
    now = datetime.now()
    if plan.get("plan_date") != now.strftime("%Y-%m-%d"):
        return False
    parsed = _parse_event_time(plan.get("remind_at_time") or "09:00")
    if not parsed:
        return now.hour >= 9 and now.hour < 10
    return _is_in_remind_window(now, parsed[0], parsed[1])


def should_remind_schedule_event(event: dict) -> bool:
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    event_date = event.get("event_date")

    if event_date not in (today, tomorrow):
        return False

    parsed_event = _parse_event_time(event.get("event_time"))
    if parsed_event and event_date == today:
        hour, minute = parsed_event
        event_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        minutes_before = event.get("remind_minutes_before") or 30
        window_start = event_dt - timedelta(minutes=minutes_before)
        window_end = event_dt + timedelta(minutes=10)
        return window_start <= now <= window_end

    if event_date == tomorrow and not event.get("event_time"):
        remind_at = _parse_event_time(event.get("remind_at_time") or "20:00")
        if remind_at:
            return _is_in_remind_window(now, remind_at[0], remind_at[1])
        return now.hour >= 20 and now.hour < 21

    if event_date == today:
        remind_at = _parse_event_time(event.get("remind_at_time") or "09:00")
        if remind_at:
            return _is_in_remind_window(now, remind_at[0], remind_at[1])
        return now.hour >= 9 and now.hour < 10

    return False


async def send_reminder(text: str):
    try:
        await bot.send_message(GROUP, text)
    except Exception as e:
        logger.error(f"Failed to send group reminder: {e}")


async def reminder_loop():
    while True:
        try:
            await asyncio.sleep(300)
            for plan in db.get_plans_to_remind():
                if not should_remind_plan(plan):
                    continue
                text = f"📋 Напоминание по плану: {plan['title']}"
                if plan.get("description"):
                    text += f"\n{plan['description']}"
                await send_reminder(text)
                db.mark_plan_reminded(plan["id"])

            for event in db.get_all_unreminded_events():
                if not should_remind_schedule_event(event):
                    continue
                time_str = f" в {event['event_time']}" if event.get("event_time") else ""
                label = "созвон" if any(w in event["title"].lower() for w in ("созвон", "call", "zoom", "meet")) else "событие"
                text = f"📅 Напоминание — {label}: {event['title']}{time_str}"
                if event.get("description"):
                    text += f"\n{event['description']}"
                await send_reminder(text)
                db.mark_event_reminded(event["id"])
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in reminder loop: {e}")


@dp.message(CommandStart(), F.chat.id == GROUP)
async def cmd_start(message: Message):
    owner_id = resolve_owner_user_id(message)
    first_name = message.from_user.first_name or "друг"
    db.set_user_fact(owner_id, "Имя", first_name)
    db.register_group_chat(GROUP, owner_id, message.chat.title)
    welcome_text = (
        f"Привет, {first_name}! Я Flora ✨\n\n"
        "Живу только в этом чате. Обращайся по имени или @упоминанию.\n"
        "Заметки, планы, расписание, поиск, сленг — всё тут."
    )
    db.add_group_message(GROUP, "assistant", welcome_text)
    await message.reply(welcome_text)


@dp.message(Command("clear"), F.chat.id == GROUP)
async def cmd_clear(message: Message):
    db.clear_group_chat_history(GROUP)
    await message.reply("История с Flora очищена. Планы и заметки на месте.")


@dp.message(Command("status"), F.chat.id == GROUP)
async def cmd_status(message: Message):
    owner_id = resolve_owner_user_id(message)
    user_facts = db.get_user_facts(owner_id)
    pending = db.list_plan_items(owner_id, status="pending", limit=100)
    slang = db.get_slang_words(owner_id, limit=5)
    style = user_facts.get("Стиль общения", "ещё изучаю")
    await message.reply(
        f"Активных планов: {len(pending)}\n"
        f"Сленга в памяти: {len(slang)}\n"
        f"Стиль: {style[:100]}"
    )


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_message(message: types.Message):
    if not is_allowed_group(message.chat.id):
        return

    text = message.text or message.caption
    if not text:
        return

    owner_id = resolve_owner_user_id(message)
    sender_name = message.from_user.first_name or message.from_user.username or "участник"
    chat_title = message.chat.title or "группа"

    db.register_group_chat(GROUP, owner_id, chat_title)

    msg_count = db.log_group_message(GROUP, sender_name, text)
    if msg_count % 20 == 0:
        asyncio.create_task(brain.analyze_group_chat(owner_id, GROUP))

    if not await is_flora_mentioned(message):
        return

    async with typing_status(bot, message.chat.id):
        async def send_intermediate(t: str):
            try:
                await message.reply(t)
            except Exception as e:
                logger.error(f"Failed to send intermediate: {e}")

        reply_text = await brain.generate_response(
            owner_id, text,
            on_intermediate_response=send_intermediate,
            chat_id=GROUP,
            sender_name=sender_name,
            is_group=True,
            chat_title=chat_title,
        )

    await message.reply(reply_text)


@dp.message(F.chat.type == "private")
async def ignore_private(message: Message):
    return


async def main():
    global bot_username
    logger.info("Starting Flora Telegram Bot...")
    logger.info(f"Allowed group chat: {GROUP}")
    me = await bot.get_me()
    bot_username = me.username or ""

    if Config.ALLOWED_USER_IDS:
        db.register_group_chat(GROUP, Config.ALLOWED_USER_IDS[0], "Flora group")

    reminder_task = asyncio.create_task(reminder_loop())
    try:
        await dp.start_polling(bot)
    finally:
        reminder_task.cancel()
        try:
            await reminder_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
