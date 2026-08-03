import sqlite3
import os
from datetime import datetime
from app.config import Config

class Database:
    def __init__(self, db_path: str = Config.DATABASE_PATH):
        self.db_path = db_path
        # Ensure the directory exists
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initialize the database tables."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Table for telegram chat history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    role TEXT, -- 'user' or 'assistant'
                    content TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Table for general facts about the user
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_facts (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Table for startup-related information
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS startup_info (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Table for Flora's self-evolution (lessons learned)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reflection_lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_name TEXT,
                    lesson TEXT,
                    success INTEGER, -- 1 for success, 0 for failure
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            conn.commit()

    # --- Chat History Methods ---
    def add_message(self, user_id: int, role: str, content: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO chat_history (user_id, role, content) VALUES (?, ?, ?)",
                (user_id, role, content)
            )
            conn.commit()

    def get_chat_history(self, user_id: int, limit: int = 20):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT role, content, timestamp FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
            )
            rows = cursor.fetchall()
            # Return in chronological order
            return [{"role": r["role"], "content": r["content"], "timestamp": r["timestamp"]} for r in reversed(rows)]

    def clear_chat_history(self, user_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
            conn.commit()

    # --- User Facts Methods ---
    def set_user_fact(self, key: str, value: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO user_facts (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, datetime.now().isoformat())
            )
            conn.commit()

    def get_user_facts(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM user_facts")
            return {row["key"]: row["value"] for row in cursor.fetchall()}

    # --- Startup Info Methods ---
    def set_startup_info(self, key: str, value: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO startup_info (key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, datetime.now().isoformat())
            )
            conn.commit()

    def get_startup_info(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM startup_info")
            return {row["key"]: row["value"] for row in cursor.fetchall()}

    # --- Reflection Methods (Self-evolution) ---
    def add_reflection_lesson(self, task_name: str, lesson: str, success: bool):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO reflection_lessons (task_name, lesson, success) VALUES (?, ?, ?)",
                (task_name, lesson, 1 if success else 0)
            )
            conn.commit()

    def get_reflection_lessons(self, limit: int = 10):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_name, lesson, success, timestamp FROM reflection_lessons ORDER BY id DESC LIMIT ?",
                (limit,)
            )
            return [
                {
                    "task_name": row["task_name"],
                    "lesson": row["lesson"],
                    "success": bool(row["success"]),
                    "timestamp": row["timestamp"]
                }
                for row in cursor.fetchall()
            ]
