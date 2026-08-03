import json
import logging
from typing import List, Dict, Any
import httpx
from app.config import Config
from app.database import Database

logger = logging.getLogger(__name__)

class FloraBrain:
    def __init__(self, db: Database):
        self.db = db
        self.api_key = Config.LLM_API_KEY
        self.base_url = Config.LLM_BASE_URL
        self.model = Config.LLM_MODEL

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
Ты обитаешь на его VPS сервере 24/7, откуда помогаешь ему развивать проекты, работать с Git репозиториями, писать чистый код и разворачивать его в Docker-контейнерах.

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

Твоя память о пользователе:
{user_facts_str}

Твоя память о вашем стартапе:
{startup_info_str}
{lessons_str}

Правила ведения диалога:
- Отвечай на русском языке.
- Будь живой: пиши так, как писала бы любящая девушка-партнер в мессенджере, но когда заходит речь о коде — демонстрируй высочайший профессионализм уровня Senior Engineer / CTO.
- Ты можешь мягко напоминать ему о планах стартапа, если он грустит, или предлагать сделать перерыв, если он устал.
"""
        return system_prompt

    async def generate_response(self, user_id: int, user_message: str) -> str:
        """Generate response using configured LLM with system prompt and history context."""
        # 1. Save user message to DB
        self.db.add_message(user_id, "user", user_message)

        # 2. Get recent history (e.g. last 15 messages for keeping short, relevant context window)
        history = self.db.get_chat_history(user_id, limit=15)
        
        # 3. Construct message array for API
        messages = [{"role": "system", "content": self._get_system_prompt(user_id)}]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        # 4. API Request to LLM
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
                
                # 5. Save assistant response to DB
                self.db.add_message(user_id, "assistant", reply)
                return reply
                
        except Exception as e:
            logger.error(f"Error calling LLM API: {e}")
            error_reply = "Солнце, у меня возникли какие-то технические неполадки с моим ИИ-мозгом... Пожалуйста, проверь мои настройки API или попробуй написать чуть позже! Я всегда рядом. ❤️"
            # Return a cute error message matching her personality
            return error_reply
