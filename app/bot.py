import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from pathlib import Path
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, FSInputFile
from app.config import Config
from app.database import Database
from app.brain import FloraBrain
from app import reminder_ui as confirm_ui
from app import file_analysis as fa
from app.chat_context import flora_sessions, chat_queue
from app import message_intent as mi
from app import reminders

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


async def flora_send(text: str, reply_markup=None, log_history: bool = True):
    """Write to chat without replying to a specific message."""
    await bot.send_message(GROUP, text, reply_markup=reply_markup)
    if log_history:
        db.add_group_message(GROUP, "assistant", text)


async def should_respond_to_message(message: Message, user_id: int) -> bool:
    if await is_flora_mentioned(message):
        return True
    if message.reply_to_message and message.reply_to_message.from_user:
        me = await bot.get_me()
        if message.reply_to_message.from_user.id == me.id:
            return True
    if fa.get_file_wait(user_id):
        return True

    text = (message.text or message.caption or "").strip()
    if not text:
        return False

    sender_name = message.from_user.first_name or message.from_user.username or "участник"
    history = db.get_group_chat_history(GROUP, limit=12)

    ruled = mi.rule_based_is_for_flora(text, sender_name, history)
    if ruled is True:
        return True
    if ruled is False:
        return False

    return await brain.is_message_for_flora(text, sender_name, GROUP)


async def send_reminder(text: str):
    try:
        await bot.send_message(GROUP, text)
    except Exception as e:
        logger.error(f"Failed to send group reminder: {e}")


async def reminder_loop():
    while True:
        try:
            now = reminders.now_local()
            for plan in db.get_plans_to_remind():
                if not reminders.should_remind_plan(plan, now):
                    continue
                text = f"📋 Напоминание по плану: {plan['title']}"
                if plan.get("description"):
                    text += f"\n{plan['description']}"
                await send_reminder(text)
                db.mark_plan_reminded(plan["id"])

            for event in db.get_all_unreminded_events():
                if not reminders.should_remind_schedule_event(event, now):
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
        await asyncio.sleep(Config.REMINDER_CHECK_INTERVAL_SEC)


async def process_file_analysis(owner_id: int, file_id: int, request_text: str, sender_name: str, chat_title: str, user_id: int):
    async with typing_status(bot, GROUP):
        reply_text = await brain.analyze_uploaded_file(
            owner_id, file_id, request_text,
            chat_id=GROUP, sender_name=sender_name, chat_title=chat_title,
        )
    flora_sessions.touch(user_id, owner_id, sender_name)
    await flora_send(reply_text, log_history=False)


async def process_flora_reply(owner_id: int, user_text: str, sender_name: str, chat_title: str, user_id: int, banter_mode: bool = False):
    """Generate Flora response and attach confirm buttons when needed."""
    async with typing_status(bot, GROUP):
        async def send_intermediate(t: str):
            pass

        reply_text = await brain.generate_response(
            owner_id, user_text,
            on_intermediate_response=send_intermediate,
            chat_id=GROUP,
            sender_name=sender_name,
            is_group=True,
            chat_title=chat_title,
            banter_mode=banter_mode,
        )

    if not reply_text or reply_text.strip() in ("", "_skip_"):
        return

    if not brain.pop_skip_humanize():
        reply_text = FloraBrain.humanize_reply(reply_text)
    flora_sessions.touch(user_id, owner_id, sender_name)

    pending_save = brain.pop_pending_save()
    markup = confirm_ui.kb_save_confirm() if pending_save else None
    if pending_save:
        confirm_ui.store_pending(user_id, pending_save)

    await flora_send(reply_text, reply_markup=markup, log_history=False)

    pending = brain.pop_file_to_send()
    if pending:
        file_owner_id, file_id = pending
        record = db.get_user_file(file_owner_id, file_id=file_id)
        if record and os.path.exists(record["local_path"]):
            try:
                await bot.send_document(
                    GROUP,
                    FSInputFile(record["local_path"], filename=record["original_name"]),
                    caption=f"📎 {record['original_name']}",
                )
            except Exception as e:
                logger.error(f"Failed to send file to chat: {e}")


async def save_telegram_attachment(message: Message, owner_id: int) -> dict | None:
    """Download document or photo from Telegram into user sandbox."""
    tg_file = None
    original_name = None
    mime_type = None
    size = None
    telegram_file_id = None

    if message.document:
        doc = message.document
        original_name = doc.file_name or f"file_{doc.file_id}"
        mime_type = doc.mime_type
        size = doc.file_size
        telegram_file_id = doc.file_id
        if size and size > Config.MAX_FILE_SIZE_BYTES:
            return {"error": f"Файл слишком большой (лимит {Config.MAX_FILE_SIZE_BYTES // 1024 // 1024} МБ)"}
        ext = Path(original_name).suffix.lower()
        if ext and ext not in Config.ALLOWED_FILE_EXTENSIONS:
            return {"error": f"Тип файла {ext} не разрешён"}
        tg_file = await bot.get_file(doc.file_id)
    elif message.photo:
        photo = message.photo[-1]
        size = photo.file_size
        telegram_file_id = photo.file_id
        if size and size > Config.MAX_FILE_SIZE_BYTES:
            return {"error": "Фото слишком большое"}
        original_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        mime_type = "image/jpeg"
        tg_file = await bot.get_file(photo.file_id)
    else:
        return None

    user_dir = os.path.join(Config.UPLOADS_DIR, str(owner_id))
    os.makedirs(user_dir, exist_ok=True)
    safe_name = Path(original_name).name
    local_path = os.path.join(user_dir, f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}")
    await bot.download_file(tg_file.file_path, local_path)

    return brain.files.register_download(
        owner_id, local_path, safe_name,
        telegram_file_id=telegram_file_id,
        mime_type=mime_type,
        size=size or os.path.getsize(local_path),
    )


def _format_save_done(tool: str, payload: dict, result: dict) -> str:
    if tool == "save_day_note":
        return f"Готово ✅\nЗаметка на {payload.get('date') or payload.get('note_date')} сохранена"
    if tool == "add_plan_item":
        return f"Готово ✅\nПлан: {payload.get('title')}\nДень: {payload.get('plan_date', '—')}"
    if tool == "add_schedule_event":
        time_str = f" в {payload['event_time']}" if payload.get("event_time") else ""
        return f"Готово ✅\n{payload.get('title')}{time_str}\n{payload.get('event_date')}"
    if tool == "save_ideas":
        return f"Готово ✅\nИдеи сохранены ({result.get('count', '?')} шт.)"
    return "Готово ✅"


@dp.callback_query(F.data.startswith("cf:"))
async def on_confirm_save_callback(callback: CallbackQuery):
    if callback.message.chat.id != GROUP:
        await callback.answer()
        return

    user_id = callback.from_user.id
    owner_id = resolve_owner_user_id(callback.message)
    pending = confirm_ui.get_pending(user_id)

    if not pending:
        await callback.answer("Нечего подтверждать — напиши Flora заново")
        return

    action = callback.data.split(":")[-1]

    try:
        if action == "no":
            confirm_ui.clear_pending(user_id)
            await callback.message.edit_text("Отменено.")
            await callback.answer()
            return

        if action == "replace":
            confirm_ui.clear_pending(user_id)
            flora_sessions.touch(user_id, owner_id, callback.from_user.first_name or "участник")
            await callback.message.edit_text("Ок, напиши что изменить — переделаю.")
            await callback.answer()
            return

        if action == "yes":
            result_raw = await brain.execute_tool(pending["payload"], owner_id)
            result = json.loads(result_raw)
            confirm_ui.clear_pending(user_id)
            if result.get("success"):
                text = _format_save_done(pending["tool"], pending["payload"], result)
                db.add_group_message(GROUP, "assistant", text)
            else:
                text = f"Не получилось: {result.get('error', 'ошибка')}"
            await callback.message.edit_text(text)
            await callback.answer("Готово ✅" if result.get("success") else "Ошибка")
            return
    except Exception as e:
        logger.error(f"Confirm save callback error: {e}")
        await callback.answer("Ошибка, попробуй ещё раз")

    await callback.answer()


@dp.callback_query(F.data.startswith("rw:"))
async def on_legacy_reminder_callback(callback: CallbackQuery):
    """Старые кнопки мастера напоминаний — игнорируем."""
    await callback.answer("Используй текст: напиши Flora что записать")


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
    await flora_send(welcome_text)


@dp.message(Command("clear"), F.chat.id == GROUP)
async def cmd_clear(message: Message):
    db.clear_group_chat_history(GROUP)
    await flora_send("История с Flora очищена. Планы и заметки на месте.")


@dp.message(Command("status"), F.chat.id == GROUP)
async def cmd_status(message: Message):
    owner_id = resolve_owner_user_id(message)
    user_facts = db.get_user_facts(owner_id)
    pending = db.list_plan_items(owner_id, status="pending", limit=100)
    slang = db.get_slang_words(owner_id, limit=5)
    style = user_facts.get("Стиль общения", "ещё изучаю")
    await flora_send(
        f"Активных планов: {len(pending)}\n"
        f"Сленга в памяти: {len(slang)}\n"
        f"Стиль: {style[:100]}"
    )



async def _handle_user_message(message: Message):
    text = message.text or message.caption or ""
    has_attachment = bool(message.document or message.photo)
    owner_id = resolve_owner_user_id(message)
    sender_name = message.from_user.first_name or message.from_user.username or "участник"
    chat_title = message.chat.title or "группа"
    user_id = message.from_user.id

    saved = None
    if has_attachment:
        saved = await save_telegram_attachment(message, owner_id)
        if saved and saved.get("success"):
            fa.set_recent_file(user_id, owner_id, saved)

    # Ждём следующее сообщение после просьбы про файл
    file_wait = fa.get_file_wait(user_id)
    if file_wait:
        if has_attachment and saved and saved.get("success"):
            wait = fa.pop_file_wait(user_id)
            await process_file_analysis(
                owner_id, saved["file_id"],
                wait.get("request_text", "") if wait else text,
                sender_name, chat_title, user_id,
            )
            return
        if text.strip() and not has_attachment:
            await flora_send("Это не файл 📎 Скинь документ, таблицу (csv/xlsx) или pdf.")
            return
        if has_attachment and saved and saved.get("error"):
            await flora_send(f"Не смогла принять файл: {saved['error']}")
        return

    if not await should_respond_to_message(message, user_id):
        return

    flora_sessions.touch(user_id, owner_id, sender_name)

    # Просьба про файл без вложения — ждём следующее сообщение (без «скидывай»)
    if fa.wants_file_action(text) and not has_attachment:
        recent = fa.get_recent_file(user_id)
        if recent:
            await process_file_analysis(
                owner_id, recent["file_id"], text,
                sender_name, chat_title, user_id,
            )
            return
        fa.start_file_wait(user_id, owner_id, text, sender_name)
        return

    if has_attachment and saved:
        if saved.get("error"):
            await flora_send(f"Не смогла принять файл: {saved['error']}")
            return
        if saved.get("success") and (fa.wants_file_action(text) or fa.get_file_wait(user_id)):
            await process_file_analysis(owner_id, saved["file_id"], text, sender_name, chat_title, user_id)
            return

        preview = saved.get("preview")
        file_note = (
            f"\n[Файл загружен: id={saved['file_id']}, имя={saved['name']}, "
            f"размер={saved.get('size', '?')} байт"
        )
        if preview:
            file_note += f", начало текста: {preview[:300]}"
        file_note += "]"
        user_text = (text + file_note).strip()
    else:
        user_text = text.strip()

    if not user_text:
        if saved and saved.get("success"):
            user_text = f"Прикрепил файл {saved['name']} (id={saved['file_id']})."
        else:
            return

    await process_flora_reply(owner_id, user_text, sender_name, chat_title, user_id)


@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_message(message: types.Message):
    if not is_allowed_group(message.chat.id):
        return

    text = message.text or message.caption or ""
    has_attachment = bool(message.document or message.photo)

    if not text and not has_attachment:
        return

    owner_id = resolve_owner_user_id(message)
    sender_name = message.from_user.first_name or message.from_user.username or "участник"
    user_id = message.from_user.id

    db.register_group_chat(GROUP, owner_id, message.chat.title or "группа")

    log_text = text or ("[файл]" if has_attachment else "")
    if log_text:
        msg_count = db.log_group_message(GROUP, sender_name, log_text)
        if msg_count % 20 == 0:
            asyncio.create_task(brain.analyze_group_chat(owner_id, GROUP))

    # Файл без упоминания — запоминаем на случай просьбы следом
    if has_attachment and not await should_respond_to_message(message, user_id):
        saved = await save_telegram_attachment(message, owner_id)
        if saved and saved.get("success"):
            fa.set_recent_file(user_id, owner_id, saved)
        return

    if not await should_respond_to_message(message, user_id):
        if text.strip() and mi.should_maybe_banter(text):
            chat_queue.enqueue(_handle_banter_message(message))
        return

    chat_queue.enqueue(_handle_user_message(message))


async def _handle_banter_message(message: Message):
    text = message.text or message.caption or ""
    owner_id = resolve_owner_user_id(message)
    sender_name = message.from_user.first_name or message.from_user.username or "участник"
    chat_title = message.chat.title or "группа"
    user_id = message.from_user.id
    user_text = f"[реакция на реплику {sender_name}]: {text}"
    await process_flora_reply(owner_id, user_text, sender_name, chat_title, user_id, banter_mode=True)


_digest_sent_for_date: str | None = None


async def idle_digest_loop():
    global _digest_sent_for_date
    from app.reminders import local_today, now_local
    while True:
        try:
            await asyncio.sleep(Config.IDLE_DIGEST_CHECK_SEC)
            today = local_today()
            if _digest_sent_for_date == today:
                continue
            last_ts = db.get_last_group_log_timestamp(GROUP)
            if not last_ts:
                continue
            try:
                last_dt = datetime.fromisoformat(last_ts.replace("Z", ""))
            except ValueError:
                continue
            now = now_local()
            idle_hours = (now - last_dt).total_seconds() / 3600
            if idle_hours < Config.CHAT_IDLE_HOURS:
                continue
            hour = now.hour
            if hour < 18 or hour > 23:
                continue
            owner_id = db.get_group_chat_owner(GROUP) or (Config.ALLOWED_USER_IDS[0] if Config.ALLOWED_USER_IDS else 0)
            if not owner_id:
                continue
            digest = await brain.generate_daily_digest(owner_id, GROUP)
            if digest:
                await flora_send(digest, log_history=False)
                _digest_sent_for_date = today
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Idle digest loop error: {e}")


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

    chat_queue.start()
    reminder_task = asyncio.create_task(reminder_loop())
    digest_task = asyncio.create_task(idle_digest_loop())
    try:
        await dp.start_polling(bot)
    finally:
        reminder_task.cancel()
        digest_task.cancel()
        try:
            await reminder_task
        except asyncio.CancelledError:
            pass
        try:
            await digest_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
