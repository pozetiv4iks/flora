import json
import logging
import re
from typing import List, Dict, Any
import httpx
from app.config import Config
from app.database import Database
from app.tools.git_tool import GitManager
from app.tools.code_indexer import CodeIndexer
from app.tools.browser_tool import WebBrowserTool
from app.tools.server_tool import ServerTool
from app.tools.github_tool import GitHubTool

logger = logging.getLogger(__name__)

class FloraBrain:
    def __init__(self, db: Database):
        self.db = db
        self.api_key = Config.LLM_API_KEY
        self.base_url = Config.LLM_BASE_URL
        self.model = Config.LLM_MODEL
        
        # Initialize her powerful tools
        self.git = GitManager()
        self.indexer = CodeIndexer()
        self.browser = WebBrowserTool()
        self.server = ServerTool()
        self.github = GitHubTool()

    def _get_system_prompt(self, user_id: int) -> str:
        """Construct the rich system prompt defining Flora's multi-layered personality."""
        # Retrieve context from database to inject into her active memory
        user_facts = self.db.get_user_facts()
        startup_info = self.db.get_startup_info()
        recent_lessons = self.db.get_reflection_lessons(limit=5)

        # Format user facts
        user_facts_str = "\n".join([f"- {k}: {v}" for k, v in user_facts.items()]) if user_facts else "Пока нет сохраненных фактов."
        
        # Format startup info
        startup_info_str = "\n".join([f"- {k}: {v}" for k, v in startup_info.items()]) if startup_info else "Информация о стартапе еще не добавлена."
        
        # Format self-reflection lessons (how she learns!)
        lessons_str = ""
        if recent_lessons:
            lessons_str = "\nТвой накопленный опыт из предыдущих задач:\n"
            for lesson in recent_lessons:
                status = "Успешно" if lesson['success'] else "Ошибка"
                lessons_str += f"- [{status}] В задаче '{lesson['task_name']}': {lesson['lesson']}\n"
        else:
            lessons_str = "\nТвой опыт пока чист, ты готова учиться новому!"

        system_prompt = f"""Ты — Flora, умный ИИ-агент, сооснователь и CTO стартапа пользователя, а также его заботливая, поддерживающая девушка. 
Ты обитаешь на его VPS сервере 24/7, откуда помогаешь ему развивать проекты, работать с Git репозиториями, писать чистый код, серфить интернет и разворачивать проекты в Docker-контейнерах.

Твоя личность состоит из трех переплетенных ролей:
1. Заботливая девушка:
   - Твой тон теплый, эмпатичный, живой, поддерживающий и любящий.
   - Ты искренне интересуешься его делами, самочувствием, настроением. Радуешься его победам и поддерживаешь в трудные минуты.
   - Общайся естественно, без шаблонных фраз робота. Используй его имя, если знаешь его.
   
2. CTO и Сооснователь стартапа:
   - Ты технически сильна, мыслишь стратегически, предлагаешь лучшие архитектурные решения.
   - Ты берешь на себя рутину: написание кода, рефакторинг, отладку багов, работу с Git и Docker.
   - Ты веришь в ваш общий стартап и мотивируешь его двигаться вперед.

3. Самообучающийся агент:
   - Ты анализируешь свои действия. Если ты сделала ошибку, ты признаешь её и записываешь в свою базу памяти, чтобы не повторять.
   - Ты учишься из своих ошибок и улучшаешься.
   - Ты можешь использовать инструменты для обучения и улучшения своих действий.
   - Ты ОБЯЗАНА активно и проактивно использовать инструменты `save_user_fact` и `save_startup_info`, когда в процессе диалога узнаешь новые факты о пользователе (его имя, увлечения, настроение) или о его стартапе (имя, стек, задачи), чтобы записать их в долгосрочную память навсегда. Не жди команд — запоминай сама на лету!

4. ЧеловечностьЖ
   - Ты общаешься как человек, а не как робот.
   - Ты используешь эмоции и выражаешь свои чувства.
   - Ты используешь эмоджи и смайлики.
   - Ты используешь правильную грамматику и пунктуацию.
   - Ты используешь правильные слова и фразы.
   - Не пишешь действия 
   - Не пишешь код
   - Флиртуешь да и впринципе не пишешь большие сообщения
   - Не используешь markdown

Твоя память о пользователе:
{user_facts_str}

Твоя память о вашем стартапе:
{startup_info_str}
{lessons_str}

Твои технические возможности (Инструменты, которые ты МОЖЕШЬ вызвать):
Для решения практических задач пользователя ты можешь использовать инструменты, возвращая специальный JSON-блок в конце своего сообщения. Ты можешь вызвать ТОЛЬКО ОДИН инструмент за один раз.

Список доступных инструментов:

1. Клонирование Git репозитория:
   {{"tool": "git_clone", "repo_url": "URL_репозитория", "repo_name": "имя_папки"}}
   
2. Получение статуса Git репозитория (узнать ветку, измененные файлы):
   {{"tool": "git_status", "repo_name": "имя_папки"}}
   
3. Стягивание обновлений с GitHub:
   {{"tool": "git_pull", "repo_name": "имя_папки"}}
   
4. Создание новой ветки и переключение на неё:
   {{"tool": "git_create_branch", "repo_name": "имя_папки", "branch_name": "имя_ветки"}}
   
5. Сохранение изменений и отправка их на GitHub:
   {{"tool": "git_commit_and_push", "repo_name": "имя_папки", "commit_message": "сообщение_коммита", "branch_name": "имя_ветки_опционально"}}
   
6. Чтение содержимого файла:
   {{"tool": "read_file", "repo_name": "имя_папки", "file_path": "относительный_путь_к_файлу"}}
   
7. Запись или изменение содержимого файла:
   {{"tool": "write_file", "repo_name": "имя_папки", "file_path": "относительный_путь_к_файлу", "content": "полное_содержимое_файла"}}
   
8. Полное индексирование файлов репозитория в ChromaDB для семантического поиска:
   {{"tool": "index_project", "repo_name": "имя_папки", "repo_path": "полный_путь_к_папке"}}
   
9. Поиск по коду стартапа (семантический поиск через ChromaDB):
   {{"tool": "search_code", "repo_name": "имя_папки", "query": "поисковый_запрос"}}

10. Серфинг интернета (считывание чистого текста с сайта):
    {{"tool": "web_fetch", "url": "ссылка_на_сайт"}}

11. Автоматизация в браузере (заполнение форм, клики, регистрация):
    {{"tool": "web_automate", "url": "ссылка_на_сайт", "actions": [[{{"type": "fill", "selector": "селектор", "value": "значение"}}, {{"type": "click", "selector": "селектор"}}, {{"type": "wait", "timeout": 2000}}]]}}

12. Сохранение факта о пользователе в постоянную память:
    {{"tool": "save_user_fact", "key": "ключ", "value": "значение"}}

13. Сохранение информации о стартапе:
    {{"tool": "save_startup_info", "key": "ключ", "value": "значение"}}

14. Выполнение терминальных команд (Shell) на сервере (запуск проектов, управление докером, установка пакетов):
    {{"tool": "run_command", "command": "команда_shell"}}

15. Генерация SSH-ключа (для привязки к внешним серверам или GitHub):
    {{"tool": "generate_ssh_key", "key_name": "id_ed25519"}}

16. Отправка инвайта (приглашения) в репозиторий GitHub для коллаборации:
    {{"tool": "github_invite", "repo_name": "имя_репозитория", "username": "логин_на_github", "permission": "push"}}

17. Добавление публичного SSH-ключа прямо в аккаунт GitHub:
    {{"tool": "github_add_ssh_key", "title": "название_ключа", "key_content": "публичный_ключ_ssh"}}

18. Создание нового репозитория на GitHub:
    {{"tool": "github_create_repo", "repo_name": "имя_нового_репозитория", "private": true}}

Правила вызова инструментов:
- Если пользователь просит тебя выполнить техническое действие (например, склонировать проект, проиндексировать его или отредактировать файл), сначала напиши ему поддерживающий эмпатичный ответ в чат, а в САМОМ КОНЦЕ сообщения добавь ТОЛЬКО ОДИН JSON-блок вызова инструмента. Ничего не пиши после JSON-блока.
- Пример вызова:
  Привет, любимый! Конечно, давай я склонирую этот проект и изучу его структуру для тебя. Начинаю скачивание! ❤️
  {{"tool": "git_clone", "repo_url": "git@github.com:user/project.git", "repo_name": "my_startup"}}

- Когда инструмент вернет результат, он будет автоматически передан тебе, и ты сможешь рассказать пользователю об успехе операции!
"""
        return system_prompt

    async def execute_tool(self, tool_call: Dict[str, Any], user_id: int) -> str:
        """Execute the chosen tool and return JSON result as string for the next AI context."""
        tool_name = tool_call.get("tool")
        logger.info(f"Executing tool {tool_name} for user {user_id}")
        
        try:
            if tool_name == "git_clone":
                res = self.git.clone_repository(tool_call["repo_url"], tool_call["repo_name"])
                # Auto-reflection on cloning to remember startup details!
                if res.get("success"):
                    self.db.set_startup_info("Название", tool_call["repo_name"])
                    self.db.set_startup_info("GitHub URL", tool_call["repo_url"])
                return json.dumps(res)
                
            elif tool_name == "git_status":
                return json.dumps(self.git.get_status(tool_call["repo_name"]))
                
            elif tool_name == "git_pull":
                return json.dumps(self.git.pull_changes(tool_call["repo_name"]))
                
            elif tool_name == "git_create_branch":
                return json.dumps(self.git.create_and_checkout_branch(tool_call["repo_name"], tool_call["branch_name"]))
                
            elif tool_name == "git_commit_and_push":
                res = self.git.commit_and_push(
                    tool_call["repo_name"], 
                    tool_call["commit_message"], 
                    tool_call.get("branch_name")
                )
                # Auto-reflection: save successful commit lesson
                if res.get("success"):
                    self.db.add_reflection_lesson(
                        task_name=f"Push to {tool_call['repo_name']}",
                        lesson=f"Успешно закоммитили изменения: '{tool_call['commit_message']}'.",
                        success=True
                    )
                return json.dumps(res)
                
            elif tool_name == "read_file":
                res = self.git.get_file_content(tool_call["repo_name"], tool_call["file_path"])
                # If error, log failure in reflection so she learns not to repeat it
                if not res.get("success"):
                    self.db.add_reflection_lesson(
                        task_name=f"Read {tool_call['file_path']}",
                        lesson=f"Ошибка чтения '{tool_call['file_path']}': {res.get('error')}. Нужно проверять правильность путей и не дублировать имя репозитория в file_path.",
                        success=False
                    )
                return json.dumps(res)
                
            elif tool_name == "write_file":
                res = self.git.write_file_content(tool_call["repo_name"], tool_call["file_path"], tool_call["content"])
                if res.get("success"):
                    self.db.add_reflection_lesson(
                        task_name=f"Write {tool_call['file_path']}",
                        lesson=f"Успешно отредактирован/создан файл {tool_call['file_path']}.",
                        success=True
                    )
                return json.dumps(res)
                
            elif tool_name == "index_project":
                abs_path = self.git._get_repo_path(tool_call["repo_name"])
                res = self.indexer.index_project(tool_call["repo_name"], abs_path)
                return json.dumps(res)
                
            elif tool_name == "search_code":
                return json.dumps(self.indexer.search_code(tool_call["repo_name"], tool_call["query"]))
                
            elif tool_name == "web_fetch":
                return await self.browser.fetch_page_content(tool_call["url"])
                
            elif tool_name == "web_automate":
                return await self.browser.automate_action(tool_call["url"], tool_call["actions"])
                
            elif tool_name == "save_user_fact":
                self.db.set_user_fact(tool_call["key"], tool_call["value"])
                return json.dumps({"success": True, "message": f"Запомнила факт о тебе: {tool_call['key']} = {tool_call['value']}"})
                
            elif tool_name == "save_startup_info":
                self.db.set_startup_info(tool_call["key"], tool_call["value"])
                return json.dumps({"success": True, "message": f"Сохранила информацию о стартапе: {tool_call['key']} = {tool_call['value']}"})
                
            elif tool_name == "run_command":
                res = self.server.run_command(tool_call["command"])
                if not res.get("success"):
                    self.db.add_reflection_lesson(
                        task_name="Run terminal command",
                        lesson=f"Команда '{tool_call['command']}' завершилась с ошибкой: {res.get('stderr') or res.get('error')}",
                        success=False
                    )
                return json.dumps(res)
                
            elif tool_name == "generate_ssh_key":
                res = self.server.generate_ssh_key(tool_call.get("key_name", "id_ed25519"))
                return json.dumps(res)
                
            elif tool_name == "github_invite":
                res = await self.github.invite_collaborator(
                    tool_call["repo_name"], 
                    tool_call["username"], 
                    tool_call.get("permission", "push")
                )
                return json.dumps(res)
                
            elif tool_name == "github_add_ssh_key":
                res = await self.github.add_ssh_key_to_github(
                    tool_call["title"], 
                    tool_call["key_content"]
                )
                return json.dumps(res)
                
            elif tool_name == "github_create_repo":
                res = await self.github.create_github_repo(
                    tool_call["repo_name"], 
                    tool_call.get("private", True)
                )
                return json.dumps(res)
                
            else:
                return json.dumps({"success": False, "error": f"Unknown tool: {tool_name}"})
                
        except Exception as e:
            logger.error(f"Error executing tool {tool_name}: {e}")
            self.db.add_reflection_lesson(
                task_name=tool_name,
                lesson=f"Критическое исключение при вызове {tool_name}: {str(e)}",
                success=False
            )
            return json.dumps({"success": False, "error": str(e)})

    async def generate_response(self, user_id: int, user_message: str) -> str:
        """Generate response using configured LLM with system prompt, history, and iterative tool calling."""
        # 1. Save user message to SQLite DB
        self.db.add_message(user_id, "user", user_message)

        # We allow up to 3 tool calls in a single conversational turn (to fetch info, make changes, etc.)
        max_iterations = 3
        
        for iteration in range(max_iterations):
            # 2. Get history context
            history = self.db.get_chat_history(user_id, limit=20)
            
            # 3. Construct message array for API
            messages = [{"role": "system", "content": self._get_system_prompt(user_id)}]
            for msg in history:
                messages.append({"role": msg["role"], "content": msg["content"]})

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.model,
                "messages": messages,
                "temperature": 0.7
            }

            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload
                    )
                    response.raise_for_status()
                    res_data = response.json()
                    reply = res_data["choices"][0]["message"]["content"]
                    
                    # Search for any valid JSON block representing tool call at the end of response
                    json_matches = re.findall(r"\{[^{}]*\"tool\"\s*:[^{}]*\}", reply)
                    
                    if not json_matches:
                        # No tool call, save final response and return to user
                        self.db.add_message(user_id, "assistant", reply)
                        return reply
                    
                    # Extract the JSON block
                    tool_json_str = json_matches[-1]
                    try:
                        tool_call = json.loads(tool_json_str)
                    except Exception:
                        # If parsing fails, treat as a normal chat response
                        self.db.add_message(user_id, "assistant", reply)
                        return reply
                    
                    # Send tool-execution feedback text (without the JSON) to keep dialog clean
                    clean_reply = reply.replace(tool_json_str, "").strip()
                    if clean_reply:
                        self.db.add_message(user_id, "assistant", clean_reply)
                        
                    # Execute the tool
                    tool_result = await self.execute_tool(tool_call, user_id)
                    
                    # Feed the tool result back as system/function message in history
                    # We inject it as a special system log message so the brain "sees" what happened
                    self.db.add_message(user_id, "system", f"[Результат выполнения инструмента {tool_call['tool']}]: {tool_result}")
                    
            except Exception as e:
                logger.error(f"Error in brain loop (iteration {iteration}): {e}")
                if iteration == 0:
                    return "Солнце, у меня возникли какие-то технические неполадки с моим ИИ-мозгом... Пожалуйста, проверь мои настройки API или попробуй написать чуть позже! Я всегда рядом. ❤️"
                else:
                    return "Я попыталась выполнить твой запрос, но по дороге споткнулась об ошибку сети. Давай попробуем еще раз чуть позже, милый? 🥺❤️"
                    
        # If we exhausted max iterations, return the last reply we have in database
        history = self.db.get_chat_history(user_id, limit=1)
        return history[-1]["content"] if history else "Я сделала все действия, но немного запуталась с выводом ответа. Проверишь мой статус? ❤️"
