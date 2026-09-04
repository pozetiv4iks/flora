"""File action sessions: wait for next message or use recently uploaded file."""
from datetime import datetime, timedelta

FILE_WAIT_TTL = timedelta(minutes=15)
RECENT_FILE_TTL = timedelta(seconds=120)

file_wait_sessions: dict[int, dict] = {}
recent_user_files: dict[int, dict] = {}


def wants_file_action(text: str) -> bool:
    lower = text.lower()
    file_words = (
        "файл", "документ", "таблиц", "excel", "csv", "pdf", "отчёт", "отчет",
        "spreadsheet", "xlsx", "вложен",
    )
    action_words = (
        "проанализиру", "разбери", "разобра", "анализ", "analyze",
        "прочитай", "посмотри", "изучи", "проверь", "открой", "разбер",
        "что в", "вытащи", "сделай с", "обработай",
    )
    has_file = any(f in lower for f in file_words)
    has_action = any(a in lower for a in action_words)
    return has_file and has_action


wants_file_analysis_request = wants_file_action


def set_recent_file(user_id: int, owner_id: int, saved: dict):
    recent_user_files[user_id] = {
        "file_id": saved["file_id"],
        "owner_id": owner_id,
        "name": saved.get("name", "file"),
        "at": datetime.now(),
    }


def get_recent_file(user_id: int) -> dict | None:
    rec = recent_user_files.get(user_id)
    if not rec:
        return None
    if datetime.now() - rec["at"] > RECENT_FILE_TTL:
        recent_user_files.pop(user_id, None)
        return None
    return rec


def start_file_wait(user_id: int, owner_id: int, request_text: str, sender_name: str, action: str = "analyze"):
    file_wait_sessions[user_id] = {
        "owner_id": owner_id,
        "request_text": request_text,
        "sender_name": sender_name,
        "action": action,
        "started_at": datetime.now(),
    }


def get_file_wait(user_id: int) -> dict | None:
    session = file_wait_sessions.get(user_id)
    if not session:
        return None
    if datetime.now() - session["started_at"] > FILE_WAIT_TTL:
        file_wait_sessions.pop(user_id, None)
        return None
    return session


def pop_file_wait(user_id: int) -> dict | None:
    session = get_file_wait(user_id)
    if session:
        file_wait_sessions.pop(user_id, None)
    return session


def clear_file_wait(user_id: int):
    file_wait_sessions.pop(user_id, None)
