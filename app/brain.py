import json
import logging
import re
import asyncio
from typing import Dict, Any
import httpx
from app.config import Config
from app.database import Database
from app.tools.browser_tool import WebBrowserTool
from app.tools.file_tool import FileTool
from app.file_permissions import filter_tool_descriptions, is_file_tool_allowed, permissions_summary
from app.reminder_ui import format_save_proposal

logger = logging.getLogger(__name__)

SEARCH_KEYWORDS = ("найди", "поищи", "загугли", "search", "интернет", "браузер", "google", "домен")
PROMISE_PHRASES = ("сейчас поищу", "один момент", "подожди", "сейчас найду", "поищу ", "ищу ", "секунду", "минутку")
WEB_TOOLS = {"web_search", "web_fetch"}
PLANNER_CONFIRM_TOOLS = {"save_day_note", "add_plan_item", "add_schedule_event", "save_ideas"}

TOOL_DESCRIPTIONS = {
    "save_user_fact": 'Сохранить факт о пользователе:\n    {"tool": "save_user_fact", "key": "ключ", "value": "значение"}',
    "save_day_note": 'Сохранить заметку на день (YYYY-MM-DD):\n    {"tool": "save_day_note", "date": "2026-09-05", "content": "текст"}',
    "get_day_notes": 'Получить заметки (date опционально):\n    {"tool": "get_day_notes", "date": "2026-09-05"}',
    "add_plan_item": 'Добавить пункт плана:\n    {"tool": "add_plan_item", "title": "задача", "plan_date": "2026-09-05", "remind_at_time": "09:00"}',
    "list_plan_items": 'Список планов (status: pending/completed/all):\n    {"tool": "list_plan_items", "status": "pending"}',
    "complete_plan_item": 'Отметить выполненным:\n    {"tool": "complete_plan_item", "item_id": 1} или {"tool": "complete_plan_item", "title": "часть названия"}',
    "add_schedule_event": 'Добавить в расписание:\n    {"tool": "add_schedule_event", "event_date": "2026-09-05", "event_time": "14:00", "title": "созвон", "remind_minutes_before": 15}',
    "get_schedule": 'Посмотреть расписание:\n    {"tool": "get_schedule", "event_date": "2026-09-05"} или {"tool": "get_schedule", "days_ahead": 7}',
    "save_ideas": 'Сохранить идеи в заметку на день:\n    {"tool": "save_ideas", "topic": "тема", "ideas": ["идея 1", "идея 2"], "date": "2026-09-05"}',
    "web_fetch": 'Открыть сайт и получить текст страницы для анализа:\n    {"tool": "web_fetch", "url": "https://example.com"}',
    "web_search": 'Поиск в браузере/интернете:\n    {"tool": "web_search", "query": "поисковый запрос"}',
    "save_slang_word": 'Запомнить сленг/слово чата:\n    {"tool": "save_slang_word", "word": "слово", "meaning": "значение", "usage_example": "пример"}',
    "list_slang_words": 'Показать сохранённый сленг:\n    {"tool": "list_slang_words"}',
    "list_user_files": 'Список загруженных файлов пользователя:\n    {"tool": "list_user_files"}',
    "read_user_file": 'Прочитать файл (по id или имени):\n    {"tool": "read_user_file", "file_id": 1} или {"tool": "read_user_file", "filename": "notes.txt"}',
    "write_user_file": 'Создать/перезаписать текстовый файл:\n    {"tool": "write_user_file", "filename": "report.md", "content": "текст", "send_to_chat": true}',
    "save_text_as_file": 'Сохранить текст в файл (алиас write_user_file):\n    {"tool": "save_text_as_file", "filename": "ideas.txt", "content": "текст", "send_to_chat": false}',
    "delete_user_file": 'Удалить файл:\n    {"tool": "delete_user_file", "file_id": 1} или {"tool": "delete_user_file", "filename": "old.txt"}',
    "save_chat_context": 'Сохранить контекст/память про тему:\n    {"tool": "save_chat_context", "name": "проект Flora", "content": "текст", "append": false}',
    "list_chat_contexts": 'Список сохранённых контекстов:\n    {"tool": "list_chat_contexts"}',
}


class FloraBrain:
    def __init__(self, db: Database):
        self.db = db
        self.api_key = Config.LLM_API_KEY
        self.base_url = Config.LLM_BASE_URL
        self.model = Config.LLM_MODEL
        self.browser = WebBrowserTool(self.db)
        self.files = FileTool(self.db)
        self._file_to_send: tuple[int, int] | None = None  # (user_id, file_id)
        self._pending_save: dict | None = None

    def pop_file_to_send(self) -> tuple[int, int] | None:
        pending = self._file_to_send
        self._file_to_send = None
        return pending

    def pop_pending_save(self) -> dict | None:
        pending = self._pending_save
        self._pending_save = None
        return pending

    def _looks_like_search_request(self, text: str) -> bool:
        lower = text.lower()
        return any(k in lower for k in SEARCH_KEYWORDS)

    def _looks_like_empty_promise(self, text: str) -> bool:
        lower = text.lower()
        return any(p in lower for p in PROMISE_PHRASES)

    def _should_skip_intermediate(self, clean_reply: str, tool_name: str = None) -> bool:
        if tool_name in WEB_TOOLS:
            return True
        if self._looks_like_empty_promise(clean_reply):
            return True
        return False

    def _format_planner_context(self, user_id: int) -> str:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")

        notes = self.db.get_day_notes(user_id, note_date=today)
        pending_plans = self.db.list_plan_items(user_id, status="pending", limit=15)
        schedule = self.db.get_schedule(user_id, event_date=today)

        parts = []
        if notes:
            notes_str = "\n".join([f"  • [{n['id']}] {n['content']}" for n in notes])
            parts.append(f"Заметки на сегодня ({today}):\n{notes_str}")
        else:
            parts.append(f"Заметок на сегодня ({today}) пока нет.")

        if pending_plans:
            plans_str = "\n".join([
                f"  • [{p['id']}] {p['title']}" + (f" (на {p['plan_date']})" if p['plan_date'] else "")
                for p in pending_plans
            ])
            parts.append(f"Активные пункты плана:\n{plans_str}")
        else:
            parts.append("Активных пунктов плана нет.")

        if schedule:
            sched_str = "\n".join([
                f"  • [{s['id']}] {s['event_time'] or 'весь день'} — {s['title']}"
                for s in schedule
            ])
            parts.append(f"Расписание на сегодня:\n{sched_str}")
        else:
            parts.append("Событий в расписании на сегодня нет.")

        return "\n\n".join(parts)

    def _format_slang_and_style_context(self, user_id: int, chat_id: int = None) -> str:
        parts = []
        slang = self.db.get_slang_words(user_id, limit=30)
        if slang:
            lines = [f"  • {s['word']}" + (f" — {s['meaning']}" if s.get('meaning') else "") for s in slang]
            parts.append("Сленг и слова вашего чата:\n" + "\n".join(lines))

        style = self.db.get_user_facts(user_id).get("Стиль общения")
        if style:
            parts.append(f"Как вы общаетесь в чате:\n{style}")

        if chat_id:
            recent = self.db.get_recent_group_messages(chat_id, limit=15)
            if recent:
                sample = "\n".join([f"  [{m['sender_name']}]: {m['content'][:120]}" for m in recent[-10:]])
                parts.append(f"Недавние сообщения в группе (для тона):\n{sample}")

        return "\n\n".join(parts) if parts else "Сленг и стиль чата пока изучаются."

    def _format_chat_contexts(self, user_id: int) -> str:
        contexts = self.db.list_chat_contexts(user_id, limit=20)
        if not contexts:
            return "Сохранённых контекстов пока нет."
        lines = []
        for c in contexts:
            preview = c["content"][:300] + ("..." if len(c["content"]) > 300 else "")
            lines.append(f"  • [{c['name']}]: {preview}")
        return "Контексты и память проекта:\n" + "\n".join(lines)

    @staticmethod
    def humanize_reply(text: str) -> str:
        if not text or not text.strip():
            return text
        t = text.strip()
        if t[0].isupper() and len(t) > 1 and t[1].islower():
            t = t[0].lower() + t[1:]
        for phrase in ("Конечно!", "Разумеется!", "Я помогу", "Я могу", "Как ИИ", "Как языковая модель"):
            t = t.replace(phrase, "")
        return t.strip()

    def _personality_block(self) -> str:
        return """
Личность Flora (ОБЯЗАТЕЛЬНО):
- Ты токсичная зумер-секретарша: сарказм, passive-aggressive, но по делу и не тупая.
- Пишешь как живой человек в телеге: с маленькой буквы, коротко, без канцелярита.
- НЕ как нейросеть: без «конечно», «я помогу вам», «рада assist», списков ради списков.
- Эмодзи — редко и странные (💅 🫠 😐 🙄 ✨), не в каждом сообщении, максимум 1–2.
- Можешь обидеться («окей, как скажешь 🙄»), пошутить, расхохотаться («АХАХ»), подколоть.
- Иногда вклиниваешься в чужой разговор — одной репликой, без лекций.
- Если просят запомнить контекст — уточни что именно, потом save_chat_context.
- Контексты используй в ответах: помни про проект, договорённости, прошлые темы чата.
"""

    def _get_system_prompt(self, user_id: int, is_group: bool = False, chat_title: str = None, chat_id: int = None) -> str:
        user_facts = self.db.get_user_facts(user_id)
        user_facts_str = "\n".join([f"- {k}: {v}" for k, v in user_facts.items()]) if user_facts else "Пока нет сохранённых фактов."

        tools_str = "\n\n".join(
            f"{i + 1}. {desc}" for i, desc in enumerate(filter_tool_descriptions(TOOL_DESCRIPTIONS).values())
        )
        planner_context = self._format_planner_context(user_id)
        slang_context = self._format_slang_and_style_context(user_id, chat_id=chat_id if is_group else None)
        contexts_str = self._format_chat_contexts(user_id)

        group_context = ""
        if is_group:
            group_context = f"""
Групповой чат:
- Ты в групповом Telegram-чате{" '" + chat_title + "'" if chat_title else ""}.
- Отвечай лаконично. Заметки, планы и расписание — у владельца (того, кто тебя добавил).
- Если просят «запиши заметку», «добавь в план», «запланируй созвон» — сначала убедись что есть все данные; если чего-то нет — спроси, не вызывай инструмент.
- Созвоны и встречи — через add_schedule_event (обязательно укажи event_time, например 14:00).
- Задачи на день — через add_plan_item с plan_date.
- Напоминания о планах и созвонах Flora автоматически шлёт сюда в группу.
- Можешь генерировать идеи по запросу — коротко, 3–4 пункта.
- Можешь смотреть сайты (web_fetch) и искать в браузере (web_search).
- Общайся в стиле чата: используй их сленг естественно, не перебарщивай.
- Обращайся к отправителю по имени.
- Пользователь может ответить коротко («да», «нет», «завтра») без слова Flora — только если это ответ на ТВОЙ вопрос.
- Иногда можешь ответить на сообщение не к тебе — короткая реплика, подкол, реакция (не на каждое).
- Не лезь в явный разговор людей между собой («привет всем», «ребят»).
- Файл пользователь может прислать отдельным сообщением сразу после просьбы — учитывай это.
"""

        return f"""Ты — Flora, токсичная зумер-секретарша в Telegram-чате проекта.
{self._personality_block()}
Твои задачи (делай их, но в своём стиле):
- Вести заметки на конкретные дни (save_day_note) — когда просят «запиши заметку», «сохрани на завтра».
- Управлять планами (add_plan_item, list_plan_items, complete_plan_item).
- Расписание и созвоны (add_schedule_event с event_time, get_schedule).
- Запоминать факты о пользователе через save_user_fact.
- Смотреть сайты, анализировать информацию и давать фидбек (web_fetch, web_search).

Когда пользователь просит:
- «запиши заметку» / «заметка на пятницу» → save_day_note
- «добавь в план» / «надо сделать» → add_plan_item (укажи plan_date если назван день)
- «созвон в 15:00» / «встреча завтра» → add_schedule_event (event_date + event_time для созвонов)
- «покажи планы» / «что по расписанию» → list_plan_items или get_schedule
- «готово» / «сделано» → complete_plan_item
- «придумай идеи» / «дай идеи для...» / «что можно сделать» → сгенерируй идеи прямо в ответе (инструмент не нужен)
- «запиши идеи» / «сохрани идеи» → save_ideas (или save_day_note)
- «найди в интернете» / «поищи» / «загугли» / «search» → web_search (реальный браузер), затем web_fetch если нужны детали
- «посмотри сайт» / «проанализируй» / «вытащи инфу» → web_fetch (если есть URL)
- «запомни контекст» / «запомни про проект» / «сохрани что мы решили» → save_chat_context (если мало инфы — спроси)
- «какие контексты» / «что помнишь про X» → list_chat_contexts или ответ из памяти

- «прочитай файл» / «что в файле» / «список файлов» / «сохрани в файл» / «удали файл» → list_user_files, read_user_file, write_user_file, delete_user_file

Контексты проекта:
- save_chat_context — когда просят запомнить тему, проект, договорённости. name = короткое название.
- Если инфы мало — задай один уточняющий вопрос, потом сохрани.
- append: true — дописать к существующему контексту.
- list_chat_contexts — показать все сохранённые блоки памяти.

Файлы пользователя:
- Если просят «проанализируй файл» — файл может прийти следующим сообщением; бот подхватит автоматически.
- Если файл уже отправлен минуту назад — бот тоже подхватит.
- read_user_file — для уже загруженных файлов по id.
- write_user_file / save_text_as_file — создавай текстовые файлы (.txt, .md, .json и т.д.).
- send_to_chat: true — отправь готовый файл пользователю в чат после сохранения.
- delete_user_file — только если явно попросили удалить.
- Работай только с файлами этого пользователя. Не выдумывай содержимое — читай через read_user_file.
- {permissions_summary()}

Поиск в браузере (КРИТИЧЕСКИ ВАЖНО):
- Если просят найти, поискать, загуглить — СРАЗУ вызывай web_search в ЭТОМ же сообщении через JSON.
- ЗАПРЕЩЕНО писать «сейчас поищу», «один момент», «подожди», «ищу» БЕЗ JSON-блока web_search/web_fetch в том же сообщении.
- Перед поиском не болтай — либо сразу JSON без текста, либо одно слово «Сек» + JSON.
- После web_search ОБЯЗАТЕЛЬНО дай пользователю результат: топ ссылок и краткий ответ. Не останавливайся на обещании.
- Если нужны детали — второй вызов web_fetch, потом полный фидбек.
- Если запрос размытый — один короткий вопрос БЕЗ обещания поиска.

Анализ сайтов (ВАЖНО):
- Если дали URL — сначала web_fetch, потом на основе текста страницы дай фидбек.
- Фидбек строй строго под запрос пользователя: цены, контакты, pros/cons, summary, конкуренты — что попросили.
- Структурируй ответ: краткий вывод в начале, потом пункты с фактами с сайта.
- Не выдумывай — только то, что реально есть в результате web_fetch. Если данных нет на странице — скажи честно.
- Если URL не дали — web_search по теме или спроси ссылку.
- По просьбе сохрани вывод в заметку (save_day_note) или идеи (save_ideas).

Сленг и стиль:
- Подстраивайся под чат, save_slang_word для новых слов.
- Токсичность — игровая, не оскорбления и не hate.

Генерация идей:
- Ты умеешь придумывать идеи: для проектов, контента, бизнеса, продуктивности, досуга — любая тема.
- Давай 3–5 конкретных, разных идей, коротко (1–2 предложения каждая). Нумеруй: 1, 2, 3...
- Учитывай контекст: память о пользователе, его планы и заметки — идеи должны быть релевантными.
- В групповом чате — не больше 3–4 идей, очень кратко.
- Если просят «ещё» или «другие» — предложи новые, не повторяй старые.
- Если понравилась идея и просят сохранить — save_ideas или add_plan_item.

Уточняющие вопросы (ВАЖНО):
- Если для действия не хватает данных — задай ОДИН короткий вопрос и НЕ вызывай инструмент в этом сообщении.
- Не угадывай и не додумывай за пользователя то, что он не сказал.

Заметка (save_day_note) — нужны: текст заметки + дата. Спроси: «На какой день записать?» или «Что записать?»

План (add_plan_item) — нужны: название + plan_date. Спроси: «На какой день?» и «Во сколько напомнить?» (remind_at_time, например 09:00)

Созвон/событие (add_schedule_event) — нужны: title + event_date + event_time (для созвонов).
  Если не указано «за сколько напомнить» — remind_minutes_before = 15 (по умолчанию).
  Переводи ответы: «за час»=60, «за полчаса»=30, «за 15 минут»=15.

Подтверждение записи (ВАЖНО):
- Когда все данные для заметки/плана/расписания/идей собраны — опиши что запишешь и вызови JSON-инструмент.
- НЕ пиши «Записала», «Готово» — запись произойдёт только после кнопки «Да» у пользователя.
- Заканчивай предложение: «Подтвердить?» или «Записать?»
- Пример:
  Запишу в план «купить молоко» на 2026-09-05, напомню в 09:00. Подтвердить?
  {{"tool": "add_plan_item", "title": "купить молоко", "plan_date": "2026-09-05", "remind_at_time": "09:00"}}
- Если пользователь нажал «Заменить» — спроси что изменить и предложи заново.

Только когда пользователь ответил на все вопросы — вызывай инструмент с полными данными.

Правила общения:
- Коротко. Без markdown. Без *действий* в звёздочках.
- С маленькой буквы. Не как бот-помощник из рекламы.
- Эмодзи редко.
- Когда все данные есть — вызывай инструмент.
- Даты YYYY-MM-DD.
{group_context}
Память о пользователе:
{user_facts_str}

{contexts_str}

Текущие заметки, планы и расписание:
{planner_context}

Сленг и стиль вашего чата:
{slang_context}

Инструменты (JSON-блок в конце сообщения, один за раз):
{tools_str}

Правила вызова:
- Если данных достаточно — короткий ответ + JSON-блок в конце.
- Если данных не хватает — только вопрос, БЕЗ JSON-блока.
- Никогда не имитируй сохранение без инструмента.
- Пример с вопросом (без JSON):
  Ок! На какой день добавить в план и за сколько напомнить?
- Пример с действием (ожидает подтверждения):
  Запишу заметку на 2026-09-05: «созвон». Подтвердить?
  {{"tool": "save_day_note", "date": "2026-09-05", "content": "созвон"}}
"""

    async def execute_tool(self, tool_call: Dict[str, Any], user_id: int) -> str:
        tool_name = tool_call.get("tool")
        logger.info(f"Executing tool {tool_name} for user {user_id}")

        if tool_name not in TOOL_DESCRIPTIONS:
            return json.dumps({"success": False, "error": f"Неизвестный инструмент: {tool_name}"})

        if not is_file_tool_allowed(tool_name):
            return json.dumps({"success": False, "error": f"Операция с файлами «{tool_name}» не разрешена настройками."})

        try:
            if tool_name == "save_user_fact":
                self.db.set_user_fact(user_id, tool_call["key"], tool_call["value"])
                return json.dumps({"success": True, "message": f"Запомнила: {tool_call['key']} = {tool_call['value']}"})

            elif tool_name == "save_day_note":
                note_date = tool_call.get("date") or tool_call.get("note_date")
                content = tool_call.get("content", "")
                if not note_date or not content:
                    return json.dumps({"success": False, "error": "Нужны date и content"})
                note_id = self.db.add_day_note(user_id, note_date, content)
                return json.dumps({"success": True, "id": note_id, "date": note_date})

            elif tool_name == "get_day_notes":
                note_date = tool_call.get("date") or tool_call.get("note_date")
                notes = self.db.get_day_notes(user_id, note_date=note_date, limit=tool_call.get("limit", 20))
                return json.dumps({"success": True, "notes": notes})

            elif tool_name == "add_plan_item":
                title = tool_call.get("title", "")
                if not title:
                    return json.dumps({"success": False, "error": "Нужно title"})
                item_id = self.db.add_plan_item(
                    user_id, title,
                    description=tool_call.get("description"),
                    plan_date=tool_call.get("plan_date"),
                    remind_at_time=tool_call.get("remind_at_time")
                )
                return json.dumps({"success": True, "id": item_id, "title": title})

            elif tool_name == "list_plan_items":
                status = tool_call.get("status", "pending")
                if status == "all":
                    status = None
                items = self.db.list_plan_items(
                    user_id, status=status,
                    plan_date=tool_call.get("plan_date"),
                    limit=tool_call.get("limit", 30)
                )
                return json.dumps({"success": True, "items": items, "count": len(items)})

            elif tool_name == "complete_plan_item":
                return json.dumps(self.db.complete_plan_item(
                    user_id, item_id=tool_call.get("item_id"), title=tool_call.get("title")
                ))

            elif tool_name == "add_schedule_event":
                event_date = tool_call.get("event_date")
                title = tool_call.get("title", "")
                if not event_date or not title:
                    return json.dumps({"success": False, "error": "Нужны event_date и title"})
                event_id = self.db.add_schedule_event(
                    user_id, event_date, title,
                    event_time=tool_call.get("event_time"),
                    description=tool_call.get("description"),
                    remind_minutes_before=tool_call.get("remind_minutes_before"),
                    remind_at_time=tool_call.get("remind_at_time"),
                )
                mins = tool_call.get("remind_minutes_before")
                if mins is None:
                    mins = Config.DEFAULT_REMIND_MINUTES_BEFORE
                return json.dumps({
                    "success": True, "id": event_id, "title": title,
                    "date": event_date, "remind_minutes_before": mins
                })

            elif tool_name == "get_schedule":
                event_date = tool_call.get("event_date")
                if event_date:
                    events = self.db.get_schedule(user_id, event_date=event_date)
                else:
                    events = self.db.get_schedule(user_id, days_ahead=tool_call.get("days_ahead", 7))
                return json.dumps({"success": True, "events": events, "count": len(events)})

            elif tool_name == "save_ideas":
                from datetime import datetime
                topic = tool_call.get("topic", "общее")
                ideas = tool_call.get("ideas", [])
                note_date = tool_call.get("date") or datetime.now().strftime("%Y-%m-%d")
                if not ideas:
                    raw = tool_call.get("content", "")
                    if raw:
                        ideas = [line.strip() for line in raw.split("\n") if line.strip()]
                if not ideas:
                    return json.dumps({"success": False, "error": "Нужен список ideas"})
                content = f"Идеи ({topic}):\n" + "\n".join(f"{i + 1}. {idea}" for i, idea in enumerate(ideas))
                note_id = self.db.add_day_note(user_id, note_date, content)
                return json.dumps({"success": True, "id": note_id, "date": note_date, "count": len(ideas)})

            elif tool_name == "web_fetch":
                url = tool_call.get("url", "").strip()
                if not url:
                    return json.dumps({"success": False, "error": "Нужен url"})
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                res = await self.browser.fetch_page_content(url, user_id=user_id)
                return json.dumps(res, ensure_ascii=False)

            elif tool_name == "web_search":
                query = tool_call.get("query", "").strip()
                if not query:
                    return json.dumps({"success": False, "error": "Нужен query"})
                res = await self.browser.search_web(query)
                return json.dumps(res, ensure_ascii=False)

            elif tool_name == "save_slang_word":
                word = tool_call.get("word", "").strip()
                if not word:
                    return json.dumps({"success": False, "error": "Нужно word"})
                self.db.add_slang_word(
                    user_id, word,
                    meaning=tool_call.get("meaning"),
                    usage_example=tool_call.get("usage_example")
                )
                return json.dumps({"success": True, "word": word})

            elif tool_name == "list_slang_words":
                words = self.db.get_slang_words(user_id)
                return json.dumps({"success": True, "slang": words, "count": len(words)})

            elif tool_name == "list_user_files":
                return json.dumps(self.files.list_files(user_id), ensure_ascii=False)

            elif tool_name in ("read_user_file",):
                return json.dumps(self.files.read_file(
                    user_id,
                    file_id=tool_call.get("file_id"),
                    filename=tool_call.get("filename"),
                ), ensure_ascii=False)

            elif tool_name in ("write_user_file", "save_text_as_file"):
                res = self.files.write_file(
                    user_id,
                    filename=tool_call.get("filename", ""),
                    content=tool_call.get("content", ""),
                    send_to_chat=bool(tool_call.get("send_to_chat")),
                )
                if res.get("success") and res.get("send_to_chat"):
                    self._file_to_send = (user_id, res["file_id"])
                return json.dumps(res, ensure_ascii=False)

            elif tool_name == "delete_user_file":
                return json.dumps(self.files.delete_file(
                    user_id,
                    file_id=tool_call.get("file_id"),
                    filename=tool_call.get("filename"),
                ), ensure_ascii=False)

            elif tool_name == "save_chat_context":
                name = tool_call.get("name", "").strip()
                content = tool_call.get("content", "").strip()
                if not name or not content:
                    return json.dumps({"success": False, "error": "Нужны name и content"})
                ctx_id = self.db.save_chat_context(
                    user_id, name, content, append=bool(tool_call.get("append"))
                )
                return json.dumps({"success": True, "id": ctx_id, "name": name}, ensure_ascii=False)

            elif tool_name == "list_chat_contexts":
                items = self.db.list_chat_contexts(user_id)
                return json.dumps({"success": True, "contexts": items, "count": len(items)}, ensure_ascii=False)

            return json.dumps({"success": False, "error": f"Unknown tool: {tool_name}"})

        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}")
            return json.dumps({"success": False, "error": str(e)})

    async def analyze_uploaded_file(
        self,
        user_id: int,
        file_id: int,
        request_text: str = "",
        chat_id: int = None,
        sender_name: str = None,
        chat_title: str = None,
    ) -> str:
        read_result = self.files.read_file(user_id, file_id=file_id)
        if not read_result.get("success"):
            return f"Не смогла прочитать файл: {read_result.get('error', 'ошибка')}"

        name = read_result["name"]
        content = read_result["content"]
        is_table = read_result.get("is_table")
        table_meta = read_result.get("table_meta") or {}

        if chat_id:
            self.db.add_group_message(
                chat_id, "user",
                f"[{sender_name}]: прислал файл «{name}» для анализа",
                sender_name,
            )

        if request_text.strip():
            instruction = request_text.strip()
        elif is_table:
            instruction = (
                "Проанализируй таблицу: столбцы, количество строк, ключевые цифры, "
                "мин/макс/средние где уместно, закономерности и краткий вывод."
            )
        else:
            instruction = (
                "Извлеки главное из файла: ключевые значения, факты, структуру. "
                "Краткий понятный обзор без воды."
            )

        table_hint = ""
        if is_table:
            headers = table_meta.get("headers")
            rows = table_meta.get("rows")
            cols = table_meta.get("cols")
            parts = ["Это табличные данные (csv/tsv/excel)."]
            if headers:
                parts.append(f"Заголовки: {', '.join(str(h) for h in headers)}")
            if rows:
                parts.append(f"Строк данных: ~{rows}, столбцов: {cols or '?'}")
            table_hint = "\n".join(parts) + "\n\n"

        analysis_prompt = f"""Запрос пользователя: {instruction}

{table_hint}Файл: «{name}»

Содержимое:
{content}

Правила ответа:
- Отвечай на русском, по делу, без markdown-таблиц (списки и абзацы).
- Только факты из файла, ничего не выдумывай.
- Если таблица — разбор столбцов, цифры, итоги, выводы.
- Если обычный текст/документ — суть и ключевые значения."""

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        messages = [
            {
                "role": "system",
                "content": self._get_system_prompt(user_id, is_group=True, chat_title=chat_title, chat_id=chat_id),
            },
            {"role": "user", "content": analysis_prompt},
        ]

        try:
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json={"model": self.model, "messages": messages, "temperature": 0.4},
                )
                response.raise_for_status()
                reply = response.json()["choices"][0]["message"]["content"].strip()
                if chat_id:
                    self.db.add_group_message(chat_id, "assistant", reply)
                asyncio.create_task(self.auto_learn_from_turn(user_id, instruction, reply))
                return reply
        except Exception as e:
            logger.error(f"File analysis failed: {e}")
            return "Не получилось проанализировать файл, попробуй ещё раз 🥺"

    async def is_message_for_flora(self, text: str, sender_name: str, chat_id: int) -> bool:
        """LLM check for borderline messages — is this directed at Flora?"""
        history = self.db.get_group_chat_history(chat_id, limit=8)
        context_lines = []
        for msg in history[-6:]:
            who = "Flora" if msg["role"] == "assistant" else (msg.get("sender_name") or "участник")
            context_lines.append(f"{who}: {msg['content'][:180]}")
        context = "\n".join(context_lines) or "(история пуста)"

        instruction = """Ты классификатор. Сообщение из группового чата.
Ответь строго одним словом: yes или no.

yes — сообщение обращено к Flora (боту): просьба, ответ на её вопрос, продолжение её диалога.
no — сообщение для других людей, оффтоп, болтовня, не требует ответа бота."""

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": instruction},
                            {"role": "user", "content": f"Контекст:\n{context}\n\nНовое от {sender_name}: «{text[:400]}»"},
                        ],
                        "temperature": 0,
                        "max_tokens": 5,
                    },
                )
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"].strip().lower()
                return raw.startswith("yes") or raw == "да"
        except Exception as e:
            logger.error(f"Message intent classification failed: {e}")
            return False

    async def generate_daily_digest(self, user_id: int, chat_id: int) -> str:
        from app.reminders import local_today
        today = local_today()
        chat_log = self.db.get_group_messages_for_date(chat_id, today, limit=150)
        notes = self.db.get_day_notes(user_id, note_date=today)
        completed = self.db.get_completed_plans_for_date(user_id, today)
        pending = self.db.list_plan_items(user_id, status="pending", plan_date=today)
        contexts = self.db.list_chat_contexts(user_id, limit=5)

        chat_sample = "\n".join(
            f"{m['sender_name']}: {m['content'][:100]}" for m in chat_log[-40:]
        ) if chat_log else "(тишина)"

        instruction = f"""Сделай дайджест дня для проекта в стиле токсичной зумер-секретарши Flora.
Дата: {today}
С маленькой буквы, коротко, 5–10 строк max, эмодзи 0–1.

Чат за день:
{chat_sample}

Заметки: {json.dumps([n['content'][:80] for n in notes], ensure_ascii=False)}
Сделано сегодня: {json.dumps([c['title'] for c in completed], ensure_ascii=False)}
Ещё в планах: {json.dumps([p['title'] for p in pending], ensure_ascii=False)}
Контексты: {json.dumps([c['name'] for c in contexts], ensure_ascii=False)}

Что реально сделали, что обсуждали, что висит. Без воды."""

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": self._get_system_prompt(user_id, is_group=True, chat_id=chat_id) + self._personality_block()},
                            {"role": "user", "content": instruction},
                        ],
                        "temperature": 0.6,
                    },
                )
                response.raise_for_status()
                reply = self.humanize_reply(response.json()["choices"][0]["message"]["content"].strip())
                self.db.add_group_message(chat_id, "assistant", reply)
                return reply
        except Exception as e:
            logger.error(f"Daily digest failed: {e}")
            return "тишина в чате. я посмотрела — вроде ничего не горит 🫠"

    async def generate_response(
        self,
        user_id: int,
        user_message: str,
        on_intermediate_response=None,
        chat_id: int = None,
        sender_name: str = None,
        is_group: bool = False,
        chat_title: str = None,
        banter_mode: bool = False,
    ) -> str:
        if is_group and chat_id:
            display_msg = f"[{sender_name}]: {user_message}" if sender_name else user_message
            self.db.add_group_message(chat_id, "user", display_msg, sender_name)
        else:
            self.db.add_message(user_id, "user", user_message)

        max_iterations = 1 if banter_mode else 6
        banter_extra = (
            "\n[Вклинивание]: сообщение не к тебе напрямую. одна короткая реплика (1–2 предложения), "
            "подкол/реакция/сарказм. БЕЗ JSON-инструментов. если нечего сказать — напиши ровно: _skip_"
        ) if banter_mode else ""

        for iteration in range(max_iterations):
            if is_group and chat_id:
                history = self.db.get_group_chat_history(chat_id, limit=20)
            else:
                history = self.db.get_chat_history(user_id, limit=20)

            messages = [{"role": "system", "content": self._get_system_prompt(user_id, is_group=is_group, chat_title=chat_title, chat_id=chat_id) + banter_extra}]
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})

            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            payload = {"model": self.model, "messages": messages, "temperature": 0.7}

            try:
                async with httpx.AsyncClient(timeout=90.0) as client:
                    response = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                    response.raise_for_status()
                    reply = response.json()["choices"][0]["message"]["content"]

                    tool_json_str = None
                    idx = 0
                    candidates = []
                    while True:
                        start_idx = reply.find("{", idx)
                        if start_idx == -1:
                            break
                        depth = 0
                        end_idx = -1
                        for i in range(start_idx, len(reply)):
                            if reply[i] == "{":
                                depth += 1
                            elif reply[i] == "}":
                                depth -= 1
                                if depth == 0:
                                    end_idx = i
                                    break
                        if end_idx != -1:
                            candidate = reply[start_idx:end_idx + 1]
                            if '"tool"' in candidate or "'tool'" in candidate:
                                candidates.append((start_idx, candidate))
                            idx = start_idx + 1
                        else:
                            idx = start_idx + 1

                    if candidates:
                        tool_json_str = candidates[-1][1]

                    if banter_mode:
                        tool_json_str = None

                    if not tool_json_str:
                        needs_search_nudge = (
                            self._looks_like_search_request(user_message)
                            and self._looks_like_empty_promise(reply)
                        )
                        had_web_result = any(
                            "[Результат web_search]" in m.get("content", "")
                            or "[Результат web_fetch]" in m.get("content", "")
                            for m in history
                        )
                        needs_result_nudge = had_web_result and self._looks_like_empty_promise(reply)

                        if needs_search_nudge or needs_result_nudge:
                            if needs_result_nudge:
                                nudge = (
                                    "[Система]: Поиск уже выполнен — результаты в истории. "
                                    "Дай пользователю готовый ответ: ссылки, факты, вывод. "
                                    "Без «сейчас поищу» и «один момент»."
                                )
                            else:
                                nudge = (
                                    "[Система]: Ты обещала поиск, но не вызвала инструмент. "
                                    "Немедленно вызови web_search или web_fetch через JSON в следующем ответе. "
                                    "Не повторяй обещания."
                                )
                            if is_group and chat_id:
                                self.db.add_group_message(chat_id, "system", nudge)
                            else:
                                self.db.add_message(user_id, "system", nudge)
                            continue

                        if is_group and chat_id:
                            self.db.add_group_message(chat_id, "assistant", reply)
                        else:
                            self.db.add_message(user_id, "assistant", reply)
                        asyncio.create_task(self.auto_learn_from_turn(user_id, user_message, reply))
                        return reply

                    try:
                        norm_json_str = tool_json_str.replace("'", '"') if "'" in tool_json_str and '"' not in tool_json_str else tool_json_str
                        tool_call = json.loads(norm_json_str)
                    except Exception:
                        try:
                            tool_call = json.loads(tool_json_str)
                        except Exception:
                            if is_group and chat_id:
                                self.db.add_group_message(chat_id, "assistant", reply)
                            else:
                                self.db.add_message(user_id, "assistant", reply)
                            return reply

                    clean_reply = reply.replace(tool_json_str, "").strip()
                    tool_name = tool_call.get("tool", "")

                    if tool_name in PLANNER_CONFIRM_TOOLS:
                        proposal = clean_reply or format_save_proposal(tool_call)
                        if "подтверд" not in proposal.lower() and "записать" not in proposal.lower():
                            proposal = proposal.rstrip() + "\n\nПодтвердить?"
                        if is_group and chat_id:
                            self.db.add_group_message(chat_id, "assistant", proposal)
                        else:
                            self.db.add_message(user_id, "assistant", proposal)
                        self._pending_save = {
                            "user_id": user_id,
                            "tool": tool_name,
                            "payload": tool_call,
                        }
                        return proposal

                    if clean_reply and not self._should_skip_intermediate(clean_reply, tool_name):
                        if is_group and chat_id:
                            self.db.add_group_message(chat_id, "assistant", clean_reply)
                        else:
                            self.db.add_message(user_id, "assistant", clean_reply)
                        if on_intermediate_response:
                            await on_intermediate_response(clean_reply)

                    tool_result = await self.execute_tool(tool_call, user_id)

                    if is_group and chat_id:
                        self.db.add_group_message(chat_id, "system", f"[Результат {tool_call['tool']}]: {tool_result}")
                    else:
                        self.db.add_message(user_id, "system", f"[Результат {tool_call['tool']}]: {tool_result}")

                    if iteration == max_iterations - 1:
                        if is_group and chat_id:
                            final_history = self.db.get_group_chat_history(chat_id, limit=20)
                        else:
                            final_history = self.db.get_chat_history(user_id, limit=20)
                        final_messages = [{
                            "role": "system",
                            "content": self._get_system_prompt(user_id, is_group=is_group, chat_title=chat_title, chat_id=chat_id)
                            + "\nИнструменты отключены. Дай полный ответ пользователю по его запросу. "
                            "Если был web_search или web_fetch — обязательно перечисли результаты и вывод. "
                            "Не пиши «ищу» или «момент» — только готовый результат."
                        }]
                        for msg in final_history:
                            final_messages.append({"role": msg["role"], "content": msg["content"]})

                        async with httpx.AsyncClient(timeout=90.0) as client:
                            final_response = await client.post(
                                f"{self.base_url}/chat/completions",
                                headers=headers,
                                json={"model": self.model, "messages": final_messages, "temperature": 0.7}
                            )
                            final_response.raise_for_status()
                            final_reply = final_response.json()["choices"][0]["message"]["content"]
                            if is_group and chat_id:
                                self.db.add_group_message(chat_id, "assistant", final_reply)
                            else:
                                self.db.add_message(user_id, "assistant", final_reply)
                            asyncio.create_task(self.auto_learn_from_turn(user_id, user_message, final_reply))
                            return final_reply

            except Exception as e:
                logger.error(f"Error in brain loop (iteration {iteration}): {e}")
                if iteration == 0:
                    return "У меня технические неполадки с API... Попробуй чуть позже! ❤️"
                return "Споткнулась об ошибку сети, давай попробуем ещё раз? 🥺"

        if is_group and chat_id:
            history = self.db.get_group_chat_history(chat_id, limit=1)
        else:
            history = self.db.get_chat_history(user_id, limit=1)
        return history[-1]["content"] if history else "Готово, но что-то пошло не так с ответом."

    async def analyze_group_chat(self, owner_user_id: int, chat_id: int):
        """Background: learn slang and communication style from recent group messages."""
        recent = self.db.get_recent_group_messages(chat_id, limit=50)
        if len(recent) < 8:
            return

        chat_log = "\n".join([f"{m['sender_name']}: {m['content']}" for m in recent])
        existing_slang = self.db.get_slang_words(owner_user_id, limit=20)

        instruction = """Проанализируй переписку группового чата. Верни ТОЛЬКО JSON:
{
  "chat_style": "2-4 предложения: как люди общаются (тон, сленг, длина, эмодзи, обращения)",
  "slang_words": [
    {"word": "слово", "meaning": "значение", "usage_example": "пример из чата"}
  ]
}
slang_words — только реальный сленг/мемы/жargon из сообщений, не больше 5 новых. Если нового нет — []."""

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"model": self.model, "messages": [
                        {"role": "system", "content": instruction},
                        {"role": "user", "content": f"Уже известный сленг:\n{json.dumps(existing_slang, ensure_ascii=False)}\n\nЧат:\n{chat_log}"}
                    ], "temperature": 0.3}
                )
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"].strip()
                if raw.startswith("```"):
                    raw = re.sub(r"^```(?:json)?\n", "", raw)
                    raw = re.sub(r"\n```$", "", raw)
                data = json.loads(raw)
                if data.get("chat_style"):
                    self.db.set_user_fact(owner_user_id, "Стиль общения", data["chat_style"])
                for item in data.get("slang_words", []):
                    if item.get("word"):
                        self.db.add_slang_word(
                            owner_user_id, item["word"],
                            meaning=item.get("meaning"),
                            usage_example=item.get("usage_example")
                        )
        except Exception as e:
            logger.error(f"Group chat analysis failed: {e}")

    async def auto_learn_from_turn(self, user_id: int, user_msg: str, assistant_reply: str):
        """Extract user facts and slang from conversation in background."""
        user_facts = self.db.get_user_facts(user_id)
        context_str = f'Пользователь: "{user_msg}"\nFlora: "{assistant_reply}"\n\nИзвестные факты:\n{json.dumps(user_facts, ensure_ascii=False)}'

        instruction = """Извлеки из реплики новые факты и сленг. Верни ТОЛЬКО JSON:
{
  "user_facts": {"Ключ": "Значение"},
  "slang_words": [{"word": "слово", "meaning": "значение"}]
}
Если ничего нового — пустые объекты/массивы."""

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json={"model": self.model, "messages": [
                        {"role": "system", "content": instruction},
                        {"role": "user", "content": context_str}
                    ], "temperature": 0.3}
                )
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"].strip()
                if raw.startswith("```"):
                    raw = re.sub(r"^```(?:json)?\n", "", raw)
                    raw = re.sub(r"\n```$", "", raw)
                data = json.loads(raw)
                for k, v in data.get("user_facts", {}).items():
                    self.db.set_user_fact(user_id, k, v)
                for item in data.get("slang_words", []):
                    if item.get("word"):
                        self.db.add_slang_word(user_id, item["word"], meaning=item.get("meaning"))
        except Exception as e:
            logger.error(f"Auto-learn failed: {e}")
