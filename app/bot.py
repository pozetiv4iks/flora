import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from app.config import Config
from app.database import Database
from app.brain import FloraBrain
from app.tools.voice_processor import VoiceProcessor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

Config.validate()
db = Database()
brain = FloraBrain(db)
voice_processor = VoiceProcessor()

bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
bot_username = ""

message_buffers = {}
debounce_tasks = {}


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


def is_allowed(user_id: int) -> bool:
    if not Config.ALLOWED_USER_IDS:
        return True
    return user_id in Config.ALLOWED_USER_IDS


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
    """Decide if a schedule event should fire now."""
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


async def send_reminder(user_id: int, text: str):
    """Send reminder to owner in DM and to all registered group chats."""
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        logger.error(f"Failed to DM reminder to {user_id}: {e}")

    for gc in db.get_registered_group_chats(user_id):
        try:
            await bot.send_message(gc["chat_id"], text)
        except Exception as e:
            logger.error(f"Failed to send group reminder to {gc['chat_id']}: {e}")


async def reminder_loop():
    """Remind about plans and scheduled calls/meetings in DM and group chats."""
    while True:
        try:
            await asyncio.sleep(300)

            for plan in db.get_plans_to_remind():
                if not should_remind_plan(plan):
                    continue
                text = f"📋 Напоминание по плану: {plan['title']}"
                if plan.get("description"):
                    text += f"\n{plan['description']}"
                await send_reminder(plan["user_id"], text)
                db.mark_plan_reminded(plan["id"])

            for event in db.get_all_unreminded_events():
                if not should_remind_schedule_event(event):
                    continue
                time_str = f" в {event['event_time']}" if event.get("event_time") else ""
                label = "созвон" if any(w in event["title"].lower() for w in ("созвон", "call", "zoom", "meet")) else "событие"
                text = f"📅 Напоминание — {label}: {event['title']}{time_str}"
                if event.get("description"):
                    text += f"\n{event['description']}"
                await send_reminder(event["user_id"], text)
                db.mark_event_reminded(event["id"])

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in reminder loop: {e}")


async def schedule_reminder_loop():
    """Alias for backward compatibility."""
    await reminder_loop()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    if not is_allowed(user_id):
        return

    first_name = message.from_user.first_name or "друг"
    db.set_user_fact(user_id, "Имя", first_name)

    welcome_text = (
        f"Привет, {first_name}! Я Flora ✨\n\n"
        "Я твой помощник: заметки, планы, расписание, идеи — всё в чате. "
        "В группе обращайся ко мне по имени или через @упоминание.\n\n"
        "Чем могу помочь?"
    )
    db.add_message(user_id, "assistant", welcome_text)
    await message.answer(welcome_text)


@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    user_id = message.from_user.id
    if not is_allowed(user_id):
        return
    db.clear_chat_history(user_id)
    await message.answer("История чата очищена. Заметки, планы и расписание остались на месте.")


@dp.message(Command("status"))
async def cmd_status(message: Message):
    user_id = message.from_user.id
    if not is_allowed(user_id):
        return

    user_facts = db.get_user_facts(user_id)
    pending = db.list_plan_items(user_id, status="pending", limit=100)
    notes = db.get_day_notes(user_id, limit=5)
    schedule = db.get_schedule(user_id, days_ahead=3)

    status_text = (
        f"Привет, {user_facts.get('Имя', 'друг')}!\n\n"
        f"Активных планов: {len(pending)}\n"
        f"Последних заметок: {len(notes)}\n"
        f"Событий на 3 дня: {len(schedule)}\n\n"
        "Я на связи."
    )
    await message.answer(status_text)


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_message(message: types.Message):
    if not message.text and not message.caption:
        return
    if not await is_flora_mentioned(message):
        return

    owner_id = resolve_owner_user_id(message)
    chat_title = message.chat.title or "групповой чат"
    db.register_group_chat(message.chat.id, owner_id, chat_title)

    user_text = message.text or message.caption
    sender_name = message.from_user.first_name or message.from_user.username or "участник"

    async with typing_status(bot, message.chat.id):
        async def send_intermediate(text: str):
            try:
                await message.reply(text)
            except Exception as e:
                logger.error(f"Failed to send group intermediate: {e}")

        reply_text = await brain.generate_response(
            owner_id, user_text,
            on_intermediate_response=send_intermediate,
            chat_id=message.chat.id,
            sender_name=sender_name,
            is_group=True,
            chat_title=chat_title
        )

    await message.reply(reply_text)


@dp.message(F.chat.type == "private", F.voice)
async def handle_voice_message(message: types.Message):
    user_id = message.from_user.id
    if not is_allowed(user_id):
        return

    async with typing_status(bot, message.chat.id):
        temp_dir = os.path.join(Config.DATA_DIR, "temp")
        os.makedirs(temp_dir, exist_ok=True)
        ogg_path = os.path.join(temp_dir, f"voice_{message.voice.file_id}.ogg")

        try:
            file_info = await bot.get_file(message.voice.file_id)
            await bot.download_file(file_info.file_path, ogg_path)
            transcribed_text = await voice_processor.transcribe_voice(ogg_path)

            if not transcribed_text.strip():
                await message.answer("Не разобрала голосовое, попробуй текстом.")
                return

            async def send_intermediate(text: str):
                try:
                    await message.answer(text)
                except Exception as e:
                    logger.error(f"Failed to send intermediate: {e}")

            reply_text = await brain.generate_response(
                user_id, f"[Голосовое]: {transcribed_text}",
                on_intermediate_response=send_intermediate
            )
            await message.answer(reply_text)
        except Exception as e:
            logger.error(f"Voice processing error: {e}")
            await message.answer("Ошибка при обработке голосового, напиши текстом.")
        finally:
            if os.path.exists(ogg_path):
                try:
                    os.remove(ogg_path)
                except Exception:
                    pass


@dp.message(F.chat.type == "private")
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    user_text = message.text

    if not user_text or not is_allowed(user_id):
        return

    if user_id not in message_buffers:
        message_buffers[user_id] = []
    message_buffers[user_id].append(user_text)

    if user_id in debounce_tasks:
        debounce_tasks[user_id].cancel()

    async def delayed_processing():
        try:
            await asyncio.sleep(2.0)
            buffered_texts = message_buffers.pop(user_id, [])
            if not buffered_texts:
                return

            combined_text = "\n".join(buffered_texts)

            async with typing_status(bot, message.chat.id):
                async def send_intermediate(text: str):
                    try:
                        await message.answer(text)
                    except Exception as e:
                        logger.error(f"Failed to send intermediate: {e}")

                reply_text = await brain.generate_response(
                    user_id, combined_text, on_intermediate_response=send_intermediate
                )

            await message.answer(reply_text)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in delayed processing: {e}")
        finally:
            debounce_tasks.pop(user_id, None)

    debounce_tasks[user_id] = asyncio.create_task(delayed_processing())


async def main():
    global bot_username
    logger.info("Starting Flora Telegram Bot...")
    me = await bot.get_me()
    bot_username = me.username or ""
    logger.info(f"Bot username: @{bot_username}")

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
