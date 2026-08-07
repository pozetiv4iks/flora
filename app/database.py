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
        """Initialize the database tables with multi-tenant subscription support."""
        default_uid = Config.ALLOWED_USER_IDS[0] if Config.ALLOWED_USER_IDS else 0
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # Table for users subscription plans
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    plan TEXT DEFAULT 'none', -- 'none', 'starter', 'pro', 'business', 'owner'
                    status TEXT DEFAULT 'active', -- 'active', 'inactive'
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Table for daily API and message limits tracking
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS usage_daily (
                    user_id INTEGER,
                    date TEXT,
                    messages INTEGER DEFAULT 0,
                    tokens INTEGER DEFAULT 0,
                    emails INTEGER DEFAULT 0,
                    calendar_actions INTEGER DEFAULT 0,
                    chat_actions INTEGER DEFAULT 0,
                    PRIMARY KEY (user_id, date)
                )
            """)
            
            # Auto-insert owner IDs from Config
            if Config.ALLOWED_USER_IDS:
                for oid in Config.ALLOWED_USER_IDS:
                    cursor.execute(
                        "INSERT OR IGNORE INTO users (telegram_id, plan, status) VALUES (?, 'owner', 'active')",
                        (oid,)
                    )
            
            # Table for telegram chat history
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    role TEXT, -- 'user' or 'assistant' or 'system'
                    content TEXT,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Migration/Creation for user_facts
            cursor.execute("PRAGMA table_info(user_facts)")
            columns = [row[1] for row in cursor.fetchall()]
            if not columns:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_facts (
                        user_id INTEGER,
                        key TEXT,
                        value TEXT,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, key)
                    )
                """)
            elif "user_id" not in columns:
                cursor.execute("ALTER TABLE user_facts RENAME TO old_user_facts")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS user_facts (
                        user_id INTEGER,
                        key TEXT,
                        value TEXT,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, key)
                    )
                """)
                cursor.execute(
                    "INSERT INTO user_facts (user_id, key, value, updated_at) SELECT ?, key, value, updated_at FROM old_user_facts",
                    (default_uid,)
                )
                cursor.execute("DROP TABLE old_user_facts")
            
            # Migration/Creation for startup_info
            cursor.execute("PRAGMA table_info(startup_info)")
            columns = [row[1] for row in cursor.fetchall()]
            if not columns:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS startup_info (
                        user_id INTEGER,
                        key TEXT,
                        value TEXT,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, key)
                    )
                """)
            elif "user_id" not in columns:
                cursor.execute("ALTER TABLE startup_info RENAME TO old_startup_info")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS startup_info (
                        user_id INTEGER,
                        key TEXT,
                        value TEXT,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, key)
                    )
                """)
                cursor.execute(
                    "INSERT INTO startup_info (user_id, key, value, updated_at) SELECT ?, key, value, updated_at FROM old_startup_info",
                    (default_uid,)
                )
                cursor.execute("DROP TABLE old_startup_info")
            
            # Migration/Creation for reflection_lessons
            cursor.execute("PRAGMA table_info(reflection_lessons)")
            columns = [row[1] for row in cursor.fetchall()]
            if not columns:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS reflection_lessons (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER DEFAULT 0,
                        task_name TEXT,
                        lesson TEXT,
                        success INTEGER, -- 1 for success, 0 for failure
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            elif "user_id" not in columns:
                cursor.execute("ALTER TABLE reflection_lessons ADD COLUMN user_id INTEGER DEFAULT 0")
                cursor.execute("UPDATE reflection_lessons SET user_id = ?", (default_uid,))
            
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

    # --- Multi-tenant Subscriptions & Plans ---
    def get_user_plan(self, user_id: int) -> dict:
        """Fetch subscription plan and status of a user. Allowed owners automatically bypass checks."""
        if Config.ALLOWED_USER_IDS and user_id in Config.ALLOWED_USER_IDS:
            return {"plan": "owner", "status": "active"}
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT plan, status FROM users WHERE telegram_id = ?", (user_id,))
            row = cursor.fetchone()
            if row:
                return {"plan": row["plan"], "status": row["status"]}
            return {"plan": "none", "status": "inactive"}

    def set_user_plan(self, user_id: int, plan: str, status: str = 'active'):
        """Directly insert/update user plan (used by administrators/owners)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO users (telegram_id, plan, status) VALUES (?, ?, ?)",
                (user_id, plan, status)
            )
            conn.commit()

    def get_daily_usage(self, user_id: int) -> dict:
        """Retrieve daily usage counts for a user on today's date."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT messages, tokens, emails, calendar_actions, chat_actions FROM usage_daily WHERE user_id = ? AND date = ?",
                (user_id, today)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return {"messages": 0, "tokens": 0, "emails": 0, "calendar_actions": 0, "chat_actions": 0}

    def increment_usage(self, user_id: int, counter_name: str, increment: int = 1):
        """Safely increment a usage counter (e.g. messages, tokens) for today."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Insert with default values if not exists
            cursor.execute(
                """
                INSERT OR IGNORE INTO usage_daily (user_id, date)
                VALUES (?, ?)
                """,
                (user_id, today)
            )
            # Update the counter
            cursor.execute(
                f"UPDATE usage_daily SET {counter_name} = {counter_name} + ? WHERE user_id = ? AND date = ?",
                (increment, user_id, today)
            )
            conn.commit()

    # --- User Facts Methods ---
    def set_user_fact(self, user_id: int, key: str, value: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO user_facts (user_id, key, value, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, key, value, datetime.now().isoformat())
            )
            conn.commit()

    def get_user_facts(self, user_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM user_facts WHERE user_id = ?", (user_id,))
            return {row["key"]: row["value"] for row in cursor.fetchall()}

    # --- Startup Info Methods ---
    def set_startup_info(self, user_id: int, key: str, value: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO startup_info (user_id, key, value, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, key, value, datetime.now().isoformat())
            )
            conn.commit()

    def get_startup_info(self, user_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM startup_info WHERE user_id = ?", (user_id,))
            return {row["key"]: row["value"] for row in cursor.fetchall()}

    # --- Reflection Methods (Self-evolution) ---
    def add_reflection_lesson(self, user_id: int, task_name: str, lesson: str, success: bool):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO reflection_lessons (user_id, task_name, lesson, success) VALUES (?, ?, ?, ?)",
                (user_id, task_name, lesson, 1 if success else 0)
            )
            conn.commit()

    def get_reflection_lessons(self, user_id: int, limit: int = 10):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_name, lesson, success, timestamp FROM reflection_lessons WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user_id, limit)
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
