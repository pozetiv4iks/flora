import asyncio
import logging
import sys
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery
from app.config import Config
from app.database import Database
from app.brain import FloraBrain
from app import reminder_ui as rw

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


async def process_flora_reply(message: Message, owner_id: int, user_text: str, sender_name: str, chat_title: str):
    """Generate Flora response and attach reminder buttons when needed."""
    async with typing_status(bot, message.chat.id):
        async def send_intermediate(t: str):
            pass  # не шлём промежуточные «сейчас поищу» — только финальный ответ

        reply_text = await brain.generate_response(
            owner_id, user_text,
            on_intermediate_response=send_intermediate,
            chat_id=GROUP,
            sender_name=sender_name,
            is_group=True,
            chat_title=chat_title,
        )

    markup = rw.keyboard_for_flora_reply(reply_text)
    if markup:
        rw.start_session(message.from_user.id)
    await message.reply(reply_text, reply_markup=markup)


def _wants_reminder_wizard(text: str) -> bool:
    lower = text.lower()
    return any(w in lower for w in (
        "напомин", "напомни", "создай напомин", "добавь напомин", "запланируй созвон", "поставь напомин",
    ))


async def _save_reminder_from_session(owner_id: int, session: dict) -> str:
    title = session.get("title") or "Напоминание"
    date = session.get("date")
    if not date:
        return "Не выбрана дата — начни заново."

    kind = session.get("kind")
    before = session.get("before")
    event_time = session.get("time")

    if kind == "plan" or (kind == "custom" and not event_time):
        remind_at = "09:00"
        if before and before.startswith("at:"):
            remind_at = before.split(":", 1)[1]
        db.add_plan_item(owner_id, title, plan_date=date, remind_at_time=remind_at)
        return f"Готово ✅\nПлан: {title}\nДень: {date}\nНапомню в {remind_at}"

    remind_minutes = 30
    remind_at_time = None
    if before:
        if before.startswith("at:"):
            remind_at_time = before.split(":", 1)[1]
        else:
            try:
                remind_minutes = int(before)
            except ValueError:
                remind_minutes = 30

    db.add_schedule_event(
        owner_id, date, title,
        event_time=event_time if event_time != "none" else None,
        remind_minutes_before=remind_minutes,
        remind_at_time=remind_at_time,
    )
    time_str = f" в {event_time}" if event_time and event_time != "none" else ""
    before_str = f"за {remind_minutes} мин" if not remind_at_time else f"в {remind_at_time}"
    return f"Готово ✅\n{title}{time_str}\n{date}, напомню {before_str}"


@dp.callback_query(F.data.startswith("rw:"))
async def on_reminder_callback(callback: CallbackQuery):
    if callback.message.chat.id != GROUP:
        await callback.answer()
        return

    owner_id = resolve_owner_user_id(callback.message)
    data = callback.data
    user_id = callback.from_user.id
    session = rw.get_session(user_id) or rw.start_session(user_id)

    try:
        if data == "rw:confirm:no":
            rw.reminder_sessions.pop(user_id, None)
            await callback.message.edit_text("Отменено.")
            await callback.answer()
            return

        if data == "rw:confirm:yes":
            result = await _save_reminder_from_session(owner_id, session)
            rw.reminder_sessions.pop(user_id, None)
            await callback.message.edit_text(result)
            await callback.answer("Готово ✅")
            return

        if data.startswith("rw:type:"):
            kind = data.split(":")[-1]
            session["kind"] = kind
            session["step"] = "title" if kind == "custom" else "date"
            if kind == "call":
                session["title"] = "Созвон"
                session["step"] = "date"
                await callback.message.edit_text("На какой день?", reply_markup=rw.kb_remind_date())
            elif kind == "plan":
                session["title"] = "Задача"
                session["step"] = "date"
                await callback.message.edit_text("На какой день?", reply_markup=rw.kb_remind_date())
            else:
                await callback.message.edit_text("Напиши Flora: «напомни [что]» — или выбери дату после текста.")
            await callback.answer()
            return

        if data.startswith("rw:date:"):
            session["date"] = data.split(":", 2)[-1]
            session["step"] = "time" if session.get("kind") == "call" else "before"
            if session.get("kind") == "call":
                await callback.message.edit_text("Во сколько?", reply_markup=rw.kb_remind_time())
            else:
                await callback.message.edit_text("За сколько напомнить?", reply_markup=rw.kb_remind_before())
            await callback.answer()
            return

        if data.startswith("rw:time:"):
            session["time"] = data.split(":", 2)[-1]
            session["step"] = "before"
            await callback.message.edit_text("За сколько напомнить?", reply_markup=rw.kb_remind_before())
            await callback.answer()
            return

        if data.startswith("rw:before:"):
            raw = data[len("rw:before:"):]
            session["before"] = raw if raw.startswith("at:") else raw
            if raw.startswith("at:"):
                session["before"] = raw
            summary = (
                f"Проверь:\n{session.get('title', '?')}\n"
                f"Дата: {session.get('date')}\n"
            )
            if session.get("time") and session["time"] != "none":
                summary += f"Время: {session['time']}\n"
            summary += f"Напоминание: {raw.replace('at:', 'в ')}"
            session["step"] = "confirm"
            await callback.message.edit_text(summary, reply_markup=rw.kb_remind_confirm("", "", None, None))
            await callback.answer()
            return

    except Exception as e:
        logger.error(f"Reminder callback error: {e}")
        await callback.answer("Ошибка, попробуй ещё раз")

    await callback.answer()


@dp.message(Command("remind"), F.chat.id == GROUP)
async def cmd_remind(message: Message):
    user_id = message.from_user.id
    rw.start_session(user_id)
    await message.reply("Какое напоминание?", reply_markup=rw.kb_remind_type())


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

    if _wants_reminder_wizard(text):
        rw.start_session(message.from_user.id)
        await message.reply("Какое напоминание?", reply_markup=rw.kb_remind_type())
        return

    await process_flora_reply(message, owner_id, text, sender_name, chat_title)


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
