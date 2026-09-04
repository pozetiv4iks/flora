import asyncio
import logging
import os
import sys
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


async def schedule_reminder_loop():
    while True:
        try:
            await asyncio.sleep(1800)
            events = db.get_all_upcoming_unreminded_events()
            for event in events:
                user_id = event["user_id"]
                time_str = f" в {event['event_time']}" if event.get("event_time") else ""
                reminder = f"Напоминание: {event['title']}{time_str} ({event['event_date']})"
                if event.get("description"):
                    reminder += f"\n{event['description']}"
                try:
                    await bot.send_message(user_id, reminder)
                    for gc in db.get_registered_group_chats(user_id):
                        try:
                            await bot.send_message(gc["chat_id"], f"Flora: {reminder}")
                        except Exception:
                            pass
                    db.mark_event_reminded(event["id"])
                except Exception as e:
                    logger.error(f"Failed to send reminder to {user_id}: {e}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Error in schedule reminder loop: {e}")


@dp.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    if not is_allowed(user_id):
        return

    first_name = message.from_user.first_name or "друг"
    db.set_user_fact(user_id, "Имя", first_name)

    welcome_text = (
        f"Привет, {first_name}! Я Flora ✨\n\n"
        "Я твой помощник: заметки на дни, планы, расписание — всё в чате. "
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

    reminder_task = asyncio.create_task(schedule_reminder_loop())
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
