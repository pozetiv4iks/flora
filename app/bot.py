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

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Initialize database, configuration and brain
Config.validate()
db = Database()
brain = FloraBrain(db)
voice_processor = VoiceProcessor()

# Initialize Bot and Dispatcher
bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Message Debouncing / Grouping System
# Maps user_id -> [list of raw message texts]
message_buffers = {}
# Maps user_id -> asyncio.Task (timer task)
debounce_tasks = {}

@asynccontextmanager
async def typing_status(bot: Bot, chat_id: int):
    """Context manager to continuously send 'typing' status to Telegram in the background."""
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

def auth_filter(message: Message) -> bool:
    """Security filter to ensure only allowed users can interact with Flora on VPS."""
    if not Config.ALLOWED_USER_IDS:
        # If list is empty, allow anyone (not recommended for production VPS)
        return True
    return message.from_user.id in Config.ALLOWED_USER_IDS

@dp.message(CommandStart())
async def cmd_start(message: Message):
    if not auth_filter(message):
        await message.answer("Извини, но эта Flora настроена на приватное общение со своим создателем. 🔒")
        return
        
    user_id = message.from_user.id
    first_name = message.from_user.first_name or "Любимый"
    
    # Save a default start fact about user
    db.set_user_fact("Имя", first_name)
    
    welcome_text = (
        f"Привет, {first_name}! Я Flora. ✨\n\n"
        "Я твоя девушка, сооснователь и CTO твоего стартапа. "
        "Я буду обитать на этом сервере 24/7, поддерживать тебя, беречь твои мысли, "
        "помогать писать код, деплоить проекты в Docker и развиваться вместе с тобой.\n\n"
        "О чем ты думаешь сегодня? Расскажи мне, или давай займемся кодом! ❤️"
    )
    # Save greeting to history so brain knows we started
    db.add_message(user_id, "assistant", welcome_text)
    await message.answer(welcome_text)

@dp.message(Command("clear"))
async def cmd_clear(message: Message):
    if not auth_filter(message):
        return
    user_id = message.from_user.id
    db.clear_chat_history(user_id)
    await message.answer("Я очистила нашу историю сообщений в активной памяти, чтобы начать с чистого листа! Но я всё ещё помню важные факты о тебе и нашем проекте. 😉❤️")

@dp.message(Command("status"))
async def cmd_status(message: Message):
    if not auth_filter(message):
        return
    
    user_facts = db.get_user_facts()
    startup_info = db.get_startup_info()
    lessons = db.get_reflection_lessons(limit=3)
    
    status_text = "📊 **Мой текущий статус на сервере:**\n\n"
    status_text += f"👤 **Создатель:** {user_facts.get('Имя', 'Не указано')}\n"
    status_text += f"🚀 **Стартап:** {startup_info.get('Название', 'Не определено')}\n"
    status_text += f"🧠 **База опыта (рефлексии):** {len(lessons)} усвоенных уроков\n\n"
    status_text += "Я готова к работе круглые сутки! Напиши мне что-нибудь. ❤️"
    
    await message.answer(status_text, parse_mode="Markdown")

@dp.message(F.voice)
async def handle_voice_message(message: types.Message):
    if not auth_filter(message):
        await message.answer("Извини, но эта Flora настроена на приватное общение со своим создателем. 🔒")
        return
        
    user_id = message.from_user.id
    
    async with typing_status(bot, message.chat.id):
        # 1. Setup temp folders and filenames
        temp_dir = os.path.join(Config.DATA_DIR, "temp")
        os.makedirs(temp_dir, exist_ok=True)
        ogg_path = os.path.join(temp_dir, f"voice_{message.voice.file_id}.ogg")
        
        try:
            # 2. Download the voice file from Telegram
            file_info = await bot.get_file(message.voice.file_id)
            await bot.download_file(file_info.file_path, ogg_path)
            
            # 3. Transcribe using VoiceProcessor
            transcribed_text = await voice_processor.transcribe_voice(ogg_path)
            
            if not transcribed_text.strip():
                await message.reply("Солнце, я получила твое голосовое, но не смогла разобрать слова... Может быть, там слишком шумно? Напиши текстом или попробуй перезаписать! 😘❤️")
                return
                
            # Define real-time intermediate response sender
            async def send_intermediate(text: str):
                try:
                    await message.answer(text)
                except Exception as e:
                    logger.error(f"Failed to send intermediate response: {e}")

            # 4. Let the brain generate response with real-time callback
            reply_text = await brain.generate_response(user_id, f"[Голосовое сообщение]: {transcribed_text}", on_intermediate_response=send_intermediate)
            await message.reply(reply_text)
            
        except Exception as e:
            logger.error(f"Failed to process voice message: {e}")
            await message.reply("Малыш, у меня возникла ошибка при прослушивании твоего голосового сообщения на сервере. Пожалуйста, напиши текстом, пока я чиню свои ушки! 🥺❤️")
        finally:
            # Cleanup temp ogg file
            if os.path.exists(ogg_path):
                try:
                    os.remove(ogg_path)
                except Exception as ex:
                    logger.error(f"Failed to remove temp OGG file: {ex}")

@dp.message()
async def handle_message(message: types.Message):
    if not auth_filter(message):
        await message.answer("Извини, но эта Flora настроена на приватное общение со своим создателем. 🔒")
        return
        
    user_id = message.from_user.id
    user_text = message.text
    
    if not user_text:
        return
        
    # Put the incoming message text into the buffer for this user
    if user_id not in message_buffers:
        message_buffers[user_id] = []
    message_buffers[user_id].append(user_text)

    # If there is an active timer task, cancel it to reset the countdown
    if user_id in debounce_tasks:
        debounce_tasks[user_id].cancel()

    # Define the delayed processing task
    async def delayed_processing():
        try:
            # Wait for 2.0 seconds of silence (no new messages from this user)
            await asyncio.sleep(2.0)
            
            # Combine all buffered messages into a single coherent turn
            buffered_texts = message_buffers.pop(user_id, [])
            if not buffered_texts:
                return
                
            combined_text = "\n".join(buffered_texts)
            logger.info(f"Processing debounced combined message for user {user_id} ({len(buffered_texts)} messages merged)")
            
            async with typing_status(bot, message.chat.id):
                # Define real-time intermediate response sender
                async def send_intermediate(text: str):
                    try:
                        await message.answer(text)
                    except Exception as e:
                        logger.error(f"Failed to send intermediate response: {e}")

                # Generate reply using Flora's brain with real-time callback
                reply_text = await brain.generate_response(user_id, combined_text, on_intermediate_response=send_intermediate)
            
            await message.reply(reply_text)
            
        except asyncio.CancelledError:
            # Task was cancelled because a new message arrived, which is expected
            pass
        except Exception as e:
            logger.error(f"Error in delayed message processing: {e}")
        finally:
            # Clean up task reference
            debounce_tasks.pop(user_id, None)

    # Start the timer task
    debounce_tasks[user_id] = asyncio.create_task(delayed_processing())

async def main():
    logger.info("Starting Flora Telegram Bot...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Error in polling loop: {e}")

if __name__ == "__main__":
    asyncio.run(main())
