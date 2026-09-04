"""Session: user asked to analyze a file, waiting for upload."""
from datetime import datetime, timedelta

FILE_WAIT_TTL = timedelta(minutes=15)

file_wait_sessions: dict[int, dict] = {}


def wants_file_analysis_request(text: str) -> bool:
    lower = text.lower()
    analyze_words = ("проанализиру", "разбери", "разобра", "анализ", "analyze", "посмотри", "изучи", "проверь")
    file_words = ("файл", "документ", "таблиц", "excel", "csv", "pdf", "отчёт", "отчет", "spreadsheet")
    return any(a in lower for a in analyze_words) and any(f in lower for f in file_words)


def start_file_wait(user_id: int, owner_id: int, request_text: str, sender_name: str):
    file_wait_sessions[user_id] = {
        "owner_id": owner_id,
        "request_text": request_text,
        "sender_name": sender_name,
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
