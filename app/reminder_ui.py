"""Inline keyboards and wizard state for reminder setup."""
from datetime import datetime, timedelta
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

reminder_sessions: dict[int, dict] = {}


def kb_remind_type() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📅 Созвон", callback_data="rw:type:call"),
            InlineKeyboardButton(text="📋 Задача", callback_data="rw:type:plan"),
        ],
        [InlineKeyboardButton(text="✏️ Своё", callback_data="rw:type:custom")],
    ])


def kb_remind_date() -> InlineKeyboardMarkup:
    today = datetime.now()
    tomorrow = today + timedelta(days=1)
    day2 = today + timedelta(days=2)
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Сегодня", callback_data=f"rw:date:{today.strftime('%Y-%m-%d')}"),
            InlineKeyboardButton(text="Завтра", callback_data=f"rw:date:{tomorrow.strftime('%Y-%m-%d')}"),
        ],
        [InlineKeyboardButton(text="Послезавтра", callback_data=f"rw:date:{day2.strftime('%Y-%m-%d')}")],
    ])


def kb_remind_time() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="09:00", callback_data="rw:time:09:00"),
            InlineKeyboardButton(text="12:00", callback_data="rw:time:12:00"),
            InlineKeyboardButton(text="15:00", callback_data="rw:time:15:00"),
        ],
        [
            InlineKeyboardButton(text="18:00", callback_data="rw:time:18:00"),
            InlineKeyboardButton(text="21:00", callback_data="rw:time:21:00"),
            InlineKeyboardButton(text="Без времени", callback_data="rw:time:none"),
        ],
    ])


def kb_remind_before() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="15 мин", callback_data="rw:before:15"),
            InlineKeyboardButton(text="30 мин", callback_data="rw:before:30"),
        ],
        [
            InlineKeyboardButton(text="1 час", callback_data="rw:before:60"),
            InlineKeyboardButton(text="2 часа", callback_data="rw:before:120"),
        ],
        [InlineKeyboardButton(text="Утром в 9:00", callback_data="rw:before:at:09:00")],
    ])


def kb_remind_confirm(title: str, date: str, time: str = None, before: str = None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="rw:confirm:yes"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="rw:confirm:no"),
        ],
    ])


def keyboard_for_flora_reply(text: str) -> InlineKeyboardMarkup | None:
    lower = text.lower()
    if "какое напоминание" in lower or "что напомнить" in lower:
        return kb_remind_type()
    if "на какой день" in lower:
        return kb_remind_date()
    if "во сколько" in lower:
        return kb_remind_time()
    if "за сколько напомнить" in lower or "за сколько напомн" in lower:
        return kb_remind_before()
    if "подтверд" in lower or "всё верно" in lower or "все верно" in lower or "проверь" in lower:
        return kb_remind_confirm("", "", None, None)
    return None


def start_session(user_id: int) -> dict:
    reminder_sessions[user_id] = {"step": "type", "kind": None, "title": None, "date": None, "time": None, "before": None}
    return reminder_sessions[user_id]


def get_session(user_id: int) -> dict | None:
    return reminder_sessions.get(user_id)
