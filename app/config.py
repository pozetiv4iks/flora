import os
from dotenv import load_dotenv

# Load .env file if it exists (for local development)
load_dotenv()

class Config:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    
    LLM_API_KEY = os.getenv("LLM_API_KEY")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")
    
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
    GITHUB_USERNAME = os.getenv("GITHUB_USERNAME")
    
    PROJECTS_DIR = os.getenv("PROJECTS_DIR", "/app/projects")
    HOST_PROJECTS_DIR = os.getenv("HOST_PROJECTS_DIR", PROJECTS_DIR)
    DATA_DIR = os.getenv("DATA_DIR", "/app/data")
    
    # SQLite Database Path
    DATABASE_PATH = os.path.join(DATA_DIR, "flora.db")
    
    # ChromaDB Vector DB Directory
    CHROMA_DB_DIR = os.path.join(DATA_DIR, "chroma")
    
    # Security: list of allowed Telegram user IDs
    ALLOWED_USER_IDS = []
    allowed_ids_str = os.getenv("ALLOWED_USER_IDS", "")
    if allowed_ids_str:
        try:
            ALLOWED_USER_IDS = [int(uid.strip()) for uid in allowed_ids_str.split(",") if uid.strip()]
        except ValueError:
            print("Warning: ALLOWED_USER_IDS has invalid integers.")

    # Единственная группа, где Flora отвечает
    ALLOWED_GROUP_CHAT_ID = -5377044950

    UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")
    MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_MB", "5")) * 1024 * 1024
    MAX_FILE_READ_CHARS = int(os.getenv("MAX_FILE_READ_CHARS", "15000"))

    _file_perms_str = os.getenv("FILE_PERMISSIONS", "read,write,list,delete")
    FILE_PERMISSIONS = {p.strip().lower() for p in _file_perms_str.split(",") if p.strip()}

    _ext_str = os.getenv(
        "ALLOWED_FILE_EXTENSIONS",
        ".txt,.md,.json,.csv,.yaml,.yml,.xml,.html,.py,.js,.ts,.pdf,.log,.env,.rtf,.jpg,.jpeg,.png,.webp",
    )
    ALLOWED_FILE_EXTENSIONS = {e.strip().lower() if e.strip().startswith(".") else f".{e.strip().lower()}"
                               for e in _ext_str.split(",") if e.strip()}

    @classmethod
    def validate(cls):
        """Validate required configuration variables."""
        missing = []
        if not cls.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not cls.LLM_API_KEY:
            missing.append("LLM_API_KEY")
        
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
