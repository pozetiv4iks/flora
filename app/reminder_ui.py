"""Inline confirm buttons for planner saves (notes, plans, schedule)."""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

pending_saves: dict[int, dict] = {}


def kb_save_confirm() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data="cf:yes"),
            InlineKeyboardButton(text="❌ Нет", callback_data="cf:no"),
            InlineKeyboardButton(text="✏️ Заменить", callback_data="cf:replace"),
        ],
    ])


def store_pending(user_id: int, pending: dict):
    pending_saves[user_id] = pending


def get_pending(user_id: int) -> dict | None:
    return pending_saves.get(user_id)


def clear_pending(user_id: int):
    pending_saves.pop(user_id, None)


def format_save_proposal(tool_call: dict) -> str:
    tool = tool_call.get("tool")
    if tool == "save_day_note":
        date = tool_call.get("date") or tool_call.get("note_date") or "?"
        content = tool_call.get("content", "")
        return f"📝 Заметка на {date}:\n{content}"
    if tool == "add_plan_item":
        title = tool_call.get("title", "")
        date = tool_call.get("plan_date") or "без даты"
        lines = [f"📋 План: {title}", f"День: {date}"]
        if tool_call.get("remind_at_time"):
            lines.append(f"Напомню в {tool_call['remind_at_time']}")
        if tool_call.get("description"):
            lines.append(tool_call["description"])
        return "\n".join(lines)
    if tool == "add_schedule_event":
        title = tool_call.get("title", "")
        date = tool_call.get("event_date") or "?"
        time = tool_call.get("event_time")
        mins = tool_call.get("remind_minutes_before") or 30
        lines = [f"📅 {title}", f"Дата: {date}"]
        if time:
            lines.append(f"Время: {time}")
        lines.append(f"Напомню за {mins} мин")
        return "\n".join(lines)
    if tool == "save_ideas":
        topic = tool_call.get("topic", "общее")
        ideas = tool_call.get("ideas") or []
        date = tool_call.get("date") or "сегодня"
        body = "\n".join(f"{i + 1}. {idea}" for i, idea in enumerate(ideas))
        return f"💡 Идеи ({topic}) на {date}:\n{body}"
    return "Записать?"
