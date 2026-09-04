"""Per-user Flora sessions and serial chat queue."""
import asyncio
from datetime import datetime, timedelta
from typing import Awaitable

SESSION_TTL = timedelta(minutes=20)


class FloraSession:
    def __init__(self):
        self._sessions: dict[int, dict] = {}

    def touch(self, user_id: int, owner_id: int, sender_name: str):
        self._sessions[user_id] = {
            "owner_id": owner_id,
            "sender_name": sender_name,
            "updated_at": datetime.now(),
        }

    def get(self, user_id: int) -> dict | None:
        s = self._sessions.get(user_id)
        if not s:
            return None
        if datetime.now() - s["updated_at"] > SESSION_TTL:
            self._sessions.pop(user_id, None)
            return None
        return s

    def is_active(self, user_id: int) -> bool:
        return self.get(user_id) is not None

    def clear(self, user_id: int):
        self._sessions.pop(user_id, None)


class ChatQueue:
    """One Flora response at a time — fair order for multiple users."""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    def start(self):
        if not self._worker_task:
            self._worker_task = asyncio.create_task(self._worker())

    async def _worker(self):
        while True:
            job = await self._queue.get()
            try:
                await job()
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"Chat queue job failed: {e}")
            finally:
                self._queue.task_done()

    def enqueue(self, coro):
        async def job():
            await coro
        self._queue.put_nowait(job)

    @property
    def pending(self) -> int:
        return self._queue.qsize()


flora_sessions = FloraSession()
chat_queue = ChatQueue()
