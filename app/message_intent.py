import random

BANTER_TRIGGERS = ("лол", "ахах", "ржу", "кринж", "жесть", "блин", "офиг", "пипец", "мда", "ну и", "серьёзно", " seriously")

QUESTION_MARKERS = (
    "?", "подтверд", "какой", "какая", "какие", "когда", "сколько", "куда",
    "укажи", "напиши", "выбери", "хочешь", "нужно ли", "можешь", "скинь",
)
SHORT_REPLIES = {
    "да", "нет", "ок", "ok", "okay", "ага", "угу", "неа", "давай", "лан",
    "yes", "no", "yep", "nope", "+", "-", "верно", "точно", "не надо",
}
FLORA_TASK_WORDS = (
    "запиши", "найди", "покажи", "напомни", "добавь", "сохрани", "проанализиру",
    "поищи", "загугли", "создай", "удали", "перенеси", "запланируй", "сделай",
    "посмотри", "разбери", "внеси", "отметь",
)
GROUP_CHAT_MARKERS = (
    "ребят", "всем", "парни", "девочки", "кто знает", "как дела", "привет всем",
)


def _flora_asked_something(text: str) -> bool:
    lower = text.lower()
    return "?" in text or any(q in lower for q in QUESTION_MARKERS)


def _last_flora_message(history: list) -> dict | None:
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            return msg
    return None


def rule_based_is_for_flora(text: str, sender_name: str, history: list) -> bool | None:
    """
    Fast heuristics. True/False = decided, None = need LLM check.
    """
    t = (text or "").strip()
    if not t:
        return None

    lower = t.lower()
    words = lower.split()

    if any(m in lower for m in GROUP_CHAT_MARKERS):
        return False

    if lower in SHORT_REPLIES:
        flora = _last_flora_message(history)
        if flora and _flora_asked_something(flora.get("content", "")):
            return True
        return False

    flora = _last_flora_message(history)
    if not flora:
        return False

    flora_text = flora.get("content", "")

    # Короткий ответ на вопрос Flora
    if _flora_asked_something(flora_text) and len(t) <= 100:
        return True

    # Последнее сообщение в истории — Flora с вопросом (диалог только что)
    if history and history[-1].get("role") == "assistant":
        if _flora_asked_something(history[-1].get("content", "")) and len(t) <= 150:
            return True

    # Просьба без имени, но Flora недавно участвовала в диалоге
    recent = history[-4:]
    flora_recent = any(m.get("role") == "assistant" for m in recent)
    if flora_recent and any(w in lower for w in FLORA_TASK_WORDS) and len(t) <= 220:
        return True

    # Длинное сообщение без обращения — скорее разговор в группе
    if len(t) > 180 and "flora" not in lower and "флора" not in lower:
        return False

    return None


def should_maybe_banter(text: str) -> bool:
    """Иногда вклинивается в чужой разговор — не часто."""
    from app.config import Config
    t = (text or "").strip()
    if len(t) < 4 or len(t) > 180:
        return False
    lower = t.lower()
    if any(m in lower for m in GROUP_CHAT_MARKERS):
        return False
    if "flora" in lower or "флора" in lower:
        return False
    trigger = any(w in lower for w in BANTER_TRIGGERS)
    chance = Config.BANTER_RESPONSE_CHANCE * (2.5 if trigger else 1.0)
    return random.random() < min(chance, 0.22)
