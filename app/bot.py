import asyncio
import logging
import os
import sys
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
    
    # 1. Show typing status while processing
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    # 2. Setup temp folders and filenames
    temp_dir = os.path.join(Config.DATA_DIR, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    ogg_path = os.path.join(temp_dir, f"voice_{message.voice.file_id}.ogg")
    
    try:
        # 3. Download the voice file from Telegram
        file_info = await bot.get_file(message.voice.file_id)
        await bot.download_file(file_info.file_path, ogg_path)
        
        # 4. Transcribe using VoiceProcessor
        transcribed_text = await voice_processor.transcribe_voice(ogg_path)
        
        if not transcribed_text.strip():
            await message.reply("Солнце, я получила твое голосовое, но не смогла разобрать слова... Может быть, там слишком шумно? Напиши текстом или попробуй перезаписать! 😘❤️")
            return
            
        # 5. Let the brain generate response for the transcribed text
        reply_text = await brain.generate_response(user_id, f"[Голосовое сообщение]: {transcribed_text}")
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
        
    # Show typing status while Flora "thinks"
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    # Generate reply using Flora's brain
    reply_text = await brain.generate_response(user_id, user_text)
    
    await message.reply(reply_text)

async def main():
    logger.info("Starting Flora Telegram Bot...")
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Error in polling loop: {e}")

if __name__ == "__main__":
    asyncio.run(main())
