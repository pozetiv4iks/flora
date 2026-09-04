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
        default_uid = Config.ALLOWED_USER_IDS[0] if Config.ALLOWED_USER_IDS else 0
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
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
            
            # Creation of browser_cookies table for session injection
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS browser_cookies (
                    user_id INTEGER,
                    domain TEXT,
                    cookies_json TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, domain)
                )
            """)
            
            # Creation of temp_browser_cookies table for large, chunked multi-message session injection
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS temp_browser_cookies (
                    user_id INTEGER,
                    domain TEXT,
                    accumulated_text TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, domain)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS day_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    note_date TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS plan_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    plan_date TEXT,
                    status TEXT DEFAULT 'pending',
                    reminded INTEGER DEFAULT 0,
                    remind_at_time TEXT DEFAULT '09:00',
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    completed_at DATETIME
                )
            """)
            cursor.execute("PRAGMA table_info(plan_items)")
            plan_columns = [row[1] for row in cursor.fetchall()]
            if plan_columns and "reminded" not in plan_columns:
                cursor.execute("ALTER TABLE plan_items ADD COLUMN reminded INTEGER DEFAULT 0")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS schedule_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    event_date TEXT NOT NULL,
                    event_time TEXT,
                    title TEXT NOT NULL,
                    description TEXT,
                    reminded INTEGER DEFAULT 0,
                    remind_minutes_before INTEGER DEFAULT 30,
                    remind_at_time TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("PRAGMA table_info(schedule_events)")
            sched_columns = [row[1] for row in cursor.fetchall()]
            if sched_columns and "remind_minutes_before" not in sched_columns:
                cursor.execute("ALTER TABLE schedule_events ADD COLUMN remind_minutes_before INTEGER DEFAULT 30")
            if sched_columns and "remind_at_time" not in sched_columns:
                cursor.execute("ALTER TABLE schedule_events ADD COLUMN remind_at_time TEXT")
            if plan_columns and "remind_at_time" not in plan_columns:
                cursor.execute("ALTER TABLE plan_items ADD COLUMN remind_at_time TEXT DEFAULT '09:00'")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_chats (
                    chat_id INTEGER PRIMARY KEY,
                    owner_user_id INTEGER NOT NULL,
                    chat_title TEXT,
                    registered_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS group_chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sender_name TEXT,
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

    # --- Browser Cookies Methods (Session Injection Support) ---
    def set_browser_cookies(self, user_id: int, domain: str, cookies_json: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO browser_cookies (user_id, domain, cookies_json, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, domain.lower().strip(), cookies_json, datetime.now().isoformat())
            )
            conn.commit()

    def get_browser_cookies(self, user_id: int, domain: str) -> str:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            # Clean domain
            clean_dom = domain.lower().strip()
            cursor.execute(
                "SELECT cookies_json FROM browser_cookies WHERE user_id = ? AND domain = ?",
                (user_id, clean_dom)
            )
            row = cursor.fetchone()
            if row:
                return row["cookies_json"]
            
            # Subdomain fallback: e.g. if we search for "www.linkedin.com" but only "linkedin.com" is stored
            cursor.execute(
                "SELECT domain, cookies_json FROM browser_cookies WHERE user_id = ?",
                (user_id,)
            )
            rows = cursor.fetchall()
            for r in rows:
                stored_dom = r["domain"]
                if clean_dom.endswith("." + stored_dom) or stored_dom.endswith("." + clean_dom):
                    return r["cookies_json"]
                    
            return None

    # --- Temp Browser Cookies Chunk Methods (Chunked Assembly) ---
    def get_temp_cookie_chunks(self, user_id: int, domain: str) -> str:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT accumulated_text FROM temp_browser_cookies WHERE user_id = ? AND domain = ?",
                (user_id, domain.lower().strip())
            )
            row = cursor.fetchone()
            if row:
                return row["accumulated_text"]
            return ""

    def set_temp_cookie_chunks(self, user_id: int, domain: str, text: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO temp_browser_cookies (user_id, domain, accumulated_text, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, domain.lower().strip(), text, datetime.now().isoformat())
            )
            conn.commit()

    def clear_temp_cookie_chunks(self, user_id: int, domain: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM temp_browser_cookies WHERE user_id = ? AND domain = ?",
                (user_id, domain.lower().strip())
            )
            conn.commit()

    # --- Day Notes ---
    def add_day_note(self, user_id: int, note_date: str, content: str) -> int:
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO day_notes (user_id, note_date, content, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, note_date, content, now, now)
            )
            conn.commit()
            return cursor.lastrowid

    def get_day_notes(self, user_id: int, note_date: str = None, limit: int = 20) -> list:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if note_date:
                cursor.execute(
                    "SELECT id, note_date, content, created_at FROM day_notes WHERE user_id = ? AND note_date = ? ORDER BY id DESC",
                    (user_id, note_date)
                )
            else:
                cursor.execute(
                    "SELECT id, note_date, content, created_at FROM day_notes WHERE user_id = ? ORDER BY note_date DESC, id DESC LIMIT ?",
                    (user_id, limit)
                )
            return [dict(row) for row in cursor.fetchall()]

    def delete_day_note(self, user_id: int, note_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM day_notes WHERE id = ? AND user_id = ?", (note_id, user_id))
            conn.commit()
            return cursor.rowcount > 0

    # --- Plan Items ---
    def add_plan_item(self, user_id: int, title: str, description: str = None, plan_date: str = None, remind_at_time: str = None) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO plan_items (user_id, title, description, plan_date, status, remind_at_time) VALUES (?, ?, ?, ?, 'pending', ?)",
                (user_id, title, description, plan_date, remind_at_time or "09:00")
            )
            conn.commit()
            return cursor.lastrowid

    def list_plan_items(self, user_id: int, status: str = None, plan_date: str = None, limit: int = 30) -> list:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT id, title, description, plan_date, status, created_at, completed_at FROM plan_items WHERE user_id = ?"
            params = [user_id]
            if status:
                query += " AND status = ?"
                params.append(status)
            if plan_date:
                query += " AND plan_date = ?"
                params.append(plan_date)
            query += " ORDER BY CASE WHEN plan_date IS NULL THEN 1 ELSE 0 END, plan_date ASC, id ASC LIMIT ?"
            params.append(limit)
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def complete_plan_item(self, user_id: int, item_id: int = None, title: str = None) -> dict:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if item_id:
                cursor.execute(
                    "SELECT id, title FROM plan_items WHERE id = ? AND user_id = ? AND status = 'pending'",
                    (item_id, user_id)
                )
            elif title:
                cursor.execute(
                    "SELECT id, title FROM plan_items WHERE user_id = ? AND status = 'pending' AND title LIKE ? ORDER BY id DESC LIMIT 1",
                    (user_id, f"%{title}%")
                )
            else:
                return {"success": False, "error": "Укажи id или название пункта плана"}
            row = cursor.fetchone()
            if not row:
                return {"success": False, "error": "Пункт плана не найден или уже выполнен"}
            now = datetime.now().isoformat()
            cursor.execute(
                "UPDATE plan_items SET status = 'completed', completed_at = ? WHERE id = ?",
                (now, row["id"])
            )
            conn.commit()
            return {"success": True, "id": row["id"], "title": row["title"]}

    def delete_plan_item(self, user_id: int, item_id: int) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM plan_items WHERE id = ? AND user_id = ?", (item_id, user_id))
            conn.commit()
            return cursor.rowcount > 0

    # --- Schedule Events ---
    def add_schedule_event(
        self, user_id: int, event_date: str, title: str,
        event_time: str = None, description: str = None,
        remind_minutes_before: int = None, remind_at_time: str = None
    ) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO schedule_events
                   (user_id, event_date, event_time, title, description, remind_minutes_before, remind_at_time)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, event_date, event_time, title, description, remind_minutes_before or 30, remind_at_time)
            )
            conn.commit()
            return cursor.lastrowid

    def get_schedule(self, user_id: int, event_date: str = None, days_ahead: int = 7) -> list:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if event_date:
                cursor.execute(
                    "SELECT id, event_date, event_time, title, description FROM schedule_events WHERE user_id = ? AND event_date = ? ORDER BY event_time ASC, id ASC",
                    (user_id, event_date)
                )
            else:
                from datetime import timedelta
                today = datetime.now().strftime("%Y-%m-%d")
                end = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
                cursor.execute(
                    "SELECT id, event_date, event_time, title, description FROM schedule_events WHERE user_id = ? AND event_date >= ? AND event_date <= ? ORDER BY event_date ASC, event_time ASC",
                    (user_id, today, end)
                )
            return [dict(row) for row in cursor.fetchall()]

    def get_upcoming_unreminded_events(self, user_id: int, within_hours: int = 24) -> list:
        from datetime import timedelta
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, user_id, event_date, event_time, title, description,
                          remind_minutes_before, remind_at_time FROM schedule_events
                   WHERE user_id = ? AND reminded = 0 AND event_date IN (?, ?)
                   ORDER BY event_date ASC, event_time ASC""",
                (user_id, today, tomorrow)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_all_unreminded_events(self) -> list:
        from datetime import timedelta
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, user_id, event_date, event_time, title, description,
                          remind_minutes_before, remind_at_time FROM schedule_events
                   WHERE reminded = 0 AND event_date IN (?, ?)
                   ORDER BY user_id, event_date ASC, event_time ASC""",
                (today, tomorrow)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_plans_to_remind(self) -> list:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, user_id, title, description, plan_date, remind_at_time FROM plan_items
                   WHERE status = 'pending' AND reminded = 0 AND plan_date = ?
                   ORDER BY user_id, id ASC""",
                (today,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def mark_plan_reminded(self, plan_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE plan_items SET reminded = 1 WHERE id = ?", (plan_id,))
            conn.commit()

    def get_all_upcoming_unreminded_events(self) -> list:
        """Backward-compatible alias."""
        return self.get_all_unreminded_events()

    def mark_event_reminded(self, event_id: int):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE schedule_events SET reminded = 1 WHERE id = ?", (event_id,))
            conn.commit()

    # --- Group Chats ---
    def register_group_chat(self, chat_id: int, owner_user_id: int, chat_title: str = None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR REPLACE INTO group_chats (chat_id, owner_user_id, chat_title, registered_at) VALUES (?, ?, ?, ?)",
                (chat_id, owner_user_id, chat_title, datetime.now().isoformat())
            )
            conn.commit()

    def get_group_chat_owner(self, chat_id: int) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT owner_user_id FROM group_chats WHERE chat_id = ?", (chat_id,))
            row = cursor.fetchone()
            return row["owner_user_id"] if row else None

    def get_registered_group_chats(self, owner_user_id: int) -> list:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT chat_id, chat_title FROM group_chats WHERE owner_user_id = ?", (owner_user_id,))
            return [dict(row) for row in cursor.fetchall()]

    def add_group_message(self, chat_id: int, role: str, content: str, sender_name: str = None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO group_chat_history (chat_id, role, content, sender_name) VALUES (?, ?, ?, ?)",
                (chat_id, role, content, sender_name)
            )
            conn.commit()

    def get_group_chat_history(self, chat_id: int, limit: int = 20) -> list:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT role, content, sender_name, timestamp FROM group_chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT ?",
                (chat_id, limit)
            )
            rows = cursor.fetchall()
            return [{"role": r["role"], "content": r["content"], "sender_name": r["sender_name"], "timestamp": r["timestamp"]} for r in reversed(rows)]
